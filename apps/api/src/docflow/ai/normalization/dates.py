"""Date normalisation — done in code, deliberately not by the model.

Models are unreliable at exactly the things that matter here:

  * `15/09/2026` is DD/MM in Bangladesh and MM/DD in the US, and a model guesses
    from context that often is not present.
  * `১৫` needs numeral folding; `আশ্বিন` needs Bengali-calendar knowledge.
  * "within 7 working days of publication" needs an anchor date the model does
    not have.

So the model returns the raw string it saw and this module turns it into a
timestamp — or, just as importantly, refuses to and says why. A wrong deadline is
worse than no deadline, because the user acts on it.

The raw text is always kept alongside the parsed value so the UI can show what the
document actually said, and a user correction becomes a labelled eval example.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser

from docflow.ai.normalization.bangla import has_bengali_calendar_month, transliterate_months
from docflow.ai.normalization.text import fold_digits, strip_invisible
from docflow.models.enums import NormalizationStatus

# A deadline further out than this, or long past, is almost certainly a
# misparse (a reference number read as a year, a phone number, an act citation).
MAX_FUTURE_YEARS = 5
MAX_PAST_YEARS = 1

_NUMERIC_DATE = re.compile(r"\b(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})\b")
# ISO 8601 is unambiguous by definition, so it bypasses locale heuristics entirely.
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?\b")
_TIME_OF_DAY = re.compile(
    r"\b(\d{1,2})[:.](\d{2})\s*(a\.?m\.?|p\.?m\.?)?|\b(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)",
    re.IGNORECASE,
)
_RELATIVE_MARKER = re.compile(
    r"\b(within|after|before|from the date of|following|next|prior to|"
    r"working days?|business days?|calendar days?)\b",
    re.IGNORECASE,
)
_DURATION = re.compile(
    r"\b(\d{1,3})\s*(working|business|calendar)?\s*(day|week|month|year)s?\b", re.IGNORECASE
)


@dataclass(slots=True)
class NormalizedDate:
    """The result of trying to turn a written date into a timestamp."""

    raw_text: str
    value: datetime | None = None
    status: NormalizationStatus = NormalizationStatus.FAILED
    alternatives: list[datetime] = field(default_factory=list)
    has_explicit_time: bool = False
    reason: str | None = None

    @property
    def is_usable(self) -> bool:
        """Only `ok` drives reminders. Ambiguous dates need user confirmation first."""
        return self.value is not None and self.status is NormalizationStatus.OK

    @property
    def confidence(self) -> float:
        return {
            NormalizationStatus.OK: 1.0,
            NormalizationStatus.AMBIGUOUS: 0.5,
            NormalizationStatus.RELATIVE_UNRESOLVED: 0.3,
            NormalizationStatus.IMPLAUSIBLE: 0.1,
            NormalizationStatus.FAILED: 0.0,
        }[self.status]


def normalize_date(
    raw_text: str,
    *,
    reference_date: datetime | None = None,
    timezone: str = "Asia/Dhaka",
    now: datetime | None = None,
) -> NormalizedDate:
    """Turn a written date into a timezone-aware timestamp, or explain why not."""
    raw = (raw_text or "").strip()
    if not raw:
        return NormalizedDate(raw_text=raw_text or "", reason="empty")

    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("Asia/Dhaka")

    now = now or datetime.now(tz)
    prepared = transliterate_months(fold_digits(strip_invisible(raw)))

    # Bengali calendar: refuse rather than guess. See bangla.py.
    if has_bengali_calendar_month(prepared):
        return NormalizedDate(
            raw_text=raw,
            status=NormalizationStatus.RELATIVE_UNRESOLVED,
            reason="bengali_calendar_month",
        )

    # ISO 8601 first: year-month-day order is fixed by the standard, so running it
    # through DMY heuristics can only introduce error.
    if (iso := _ISO_DATE.search(prepared)) is not None:
        year, month, day = int(iso.group(1)), int(iso.group(2)), int(iso.group(3))
        hour, minute = int(iso.group(4) or 0), int(iso.group(5) or 0)
        try:
            parsed_iso = datetime(year, month, day, hour, minute, tzinfo=tz)
        except ValueError:
            return NormalizedDate(raw_text=raw, reason="invalid_iso_date")
        return _finalize(raw, parsed_iso, tz, now, has_time=iso.group(4) is not None)

    # Relative expressions need an anchor.
    if _RELATIVE_MARKER.search(prepared) and not _has_explicit_calendar_date(prepared):
        if reference_date is None:
            return NormalizedDate(
                raw_text=raw,
                status=NormalizationStatus.RELATIVE_UNRESOLVED,
                reason="relative_without_anchor",
            )
        resolved = _resolve_relative(prepared, reference_date.astimezone(tz))
        if resolved is None:
            return NormalizedDate(
                raw_text=raw,
                status=NormalizationStatus.RELATIVE_UNRESOLVED,
                reason="unparsed_relative",
            )
        return _finalize(raw, resolved, tz, now, has_time=False)

    settings = {
        "DATE_ORDER": "DMY",  # explicit locale policy, never inferred
        "PREFER_DATES_FROM": "future",  # deadlines point forward
        "PREFER_DAY_OF_MONTH": "first",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": timezone,
        "TO_TIMEZONE": timezone,
        "RELATIVE_BASE": (reference_date or now).astimezone(tz).replace(tzinfo=None),
    }

    parsed = dateparser.parse(prepared, languages=["en"], settings=settings)
    if parsed is None:
        return NormalizedDate(raw_text=raw, reason="unparsed")

    result = _finalize(raw, parsed, tz, now, has_time=bool(_TIME_OF_DAY.search(prepared)))

    # Ambiguity is detected after parsing so an implausible reading is rejected first.
    if result.status is NormalizationStatus.OK:
        alternative = _ambiguous_alternative(prepared, settings, timezone)
        if alternative is not None and alternative.date() != result.value.date():  # type: ignore[union-attr]
            result.status = NormalizationStatus.AMBIGUOUS
            result.alternatives = [alternative]
            result.reason = "day_month_order_ambiguous"

    return result


def _finalize(
    raw: str, parsed: datetime, tz: ZoneInfo, now: datetime, *, has_time: bool
) -> NormalizedDate:
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    parsed = parsed.astimezone(tz)

    # A date with no stated time is due at end of day, not 00:00 — otherwise a
    # "submit by 15 September" deadline silently expires the night before.
    if not has_time:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=0)

    if parsed > now + timedelta(days=365 * MAX_FUTURE_YEARS):
        return NormalizedDate(
            raw_text=raw,
            value=parsed,
            status=NormalizationStatus.IMPLAUSIBLE,
            reason="too_far_future",
            has_explicit_time=has_time,
        )
    if parsed < now - timedelta(days=365 * MAX_PAST_YEARS):
        return NormalizedDate(
            raw_text=raw,
            value=parsed,
            status=NormalizationStatus.IMPLAUSIBLE,
            reason="too_far_past",
            has_explicit_time=has_time,
        )

    return NormalizedDate(
        raw_text=raw,
        value=parsed,
        status=NormalizationStatus.OK,
        has_explicit_time=has_time,
    )


def _has_explicit_calendar_date(text: str) -> bool:
    if _NUMERIC_DATE.search(text):
        return True
    return bool(
        re.search(
            r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}|"
            r"\d{1,2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*",
            text,
            re.IGNORECASE,
        )
    )


def _resolve_relative(text: str, anchor: datetime) -> datetime | None:
    """Resolve "within N days/weeks/months" against an anchor date.

    Working days are approximated as calendar days x 7/5 and the result is nudged
    off a weekend. Bangladesh's weekend is Friday-Saturday, and this is an
    approximation we surface rather than hide — the status stays visible in the UI.
    """
    match = _DURATION.search(text)
    if match is None:
        return None

    amount = int(match.group(1))
    is_working = bool(match.group(2)) and match.group(2).lower() in ("working", "business")
    unit = match.group(3).lower()

    if unit == "day":
        days = round(amount * 7 / 5) if is_working else amount
    elif unit == "week":
        days = amount * 7
    elif unit == "month":
        days = amount * 30
    else:
        days = amount * 365

    result = anchor + timedelta(days=days)
    while result.weekday() in (4, 5):  # Friday, Saturday
        result += timedelta(days=1)
    return result.replace(hour=23, minute=59, second=59, microsecond=0)


def _ambiguous_alternative(
    text: str, settings: dict[str, object], timezone: str
) -> datetime | None:
    """Return the MDY reading if the numeric date is genuinely ambiguous.

    `15/09/2026` is unambiguous (15 cannot be a month). `05/09/2026` is not, and a
    user must confirm rather than have us silently pick one.
    """
    match = _NUMERIC_DATE.search(text)
    if match is None:
        return None

    first, second = int(match.group(1)), int(match.group(2))
    if first > 12 or second > 12 or first == second:
        return None

    mdy_settings = {**settings, "DATE_ORDER": "MDY"}
    return dateparser.parse(text, languages=["en"], settings=mdy_settings)


def to_utc(value: datetime | None) -> datetime | None:
    return value.astimezone(UTC) if value else None
