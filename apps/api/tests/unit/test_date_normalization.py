"""Date normalisation.

A wrong deadline is worse than no deadline, because the user acts on it. These
tests exist to prove the module refuses when it should, not only that it parses
when it can.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from docflow.ai.normalization.bangla import (
    bengali_char_ratio,
    detect_language,
    has_deadline_marker,
    transliterate_months,
)
from docflow.ai.normalization.dates import normalize_date
from docflow.models.enums import NormalizationStatus

DHAKA = ZoneInfo("Asia/Dhaka")
NOW = datetime(2026, 8, 20, 10, 0, tzinfo=DHAKA)


def parse(text: str, **kw):  # type: ignore[no-untyped-def]
    kw.setdefault("now", NOW)
    return normalize_date(text, **kw)


# --------------------------------------------------------------------------
# the happy paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "15 September 2026",
        "15th September 2026",
        "September 15, 2026",
        "15/09/2026",
        "15-09-2026",
        "2026-09-15",
    ],
)
def test_common_formats_resolve_to_the_same_day(text: str) -> None:
    result = parse(text)
    assert result.status is NormalizationStatus.OK, f"{text} -> {result.reason}"
    assert result.value is not None
    assert (result.value.year, result.value.month, result.value.day) == (2026, 9, 15)


def test_bangla_numerals_and_month() -> None:
    """১৫ সেপ্টেম্বর ২০২৬ — the wedge's most common date form."""
    result = parse("১৫ সেপ্টেম্বর ২০২৬")
    assert result.status is NormalizationStatus.OK
    assert (result.value.month, result.value.day) == (9, 15)


def test_date_without_time_is_due_end_of_day() -> None:
    """Otherwise a 'submit by 15 September' deadline expires the night before."""
    result = parse("15 September 2026")
    assert (result.value.hour, result.value.minute) == (23, 59)
    assert result.has_explicit_time is False


def test_explicit_time_is_preserved() -> None:
    result = parse("15 September 2026 at 5:00 PM")
    assert result.status is NormalizationStatus.OK
    assert result.has_explicit_time is True
    assert result.value.hour == 17


def test_result_is_timezone_aware_in_workspace_timezone() -> None:
    result = parse("15 September 2026")
    assert result.value.tzinfo is not None
    assert result.value.utcoffset().total_seconds() == 6 * 3600  # Asia/Dhaka


# --------------------------------------------------------------------------
# the refusals — the half that protects the user
# --------------------------------------------------------------------------


def test_day_month_ambiguity_is_flagged_not_guessed() -> None:
    """05/09/2026 is 5 Sept under DMY and 9 May under MDY. Ask, do not guess."""
    result = parse("05/09/2026")
    assert result.status is NormalizationStatus.AMBIGUOUS
    assert result.alternatives, "the alternative reading must be offered to the user"
    assert result.is_usable is False, "an ambiguous date must not drive reminders"


def test_unambiguous_numeric_date_is_not_flagged() -> None:
    """15 cannot be a month, so 15/09/2026 needs no confirmation."""
    assert parse("15/09/2026").status is NormalizationStatus.OK


def test_relative_date_without_anchor_is_unresolved() -> None:
    result = parse("within 7 working days of publication")
    assert result.status is NormalizationStatus.RELATIVE_UNRESOLVED
    assert result.value is None


def test_relative_date_resolves_against_publication_date() -> None:
    published = datetime(2026, 8, 3, tzinfo=DHAKA)  # a Monday
    result = parse("within 7 working days of publication", reference_date=published)
    assert result.status is NormalizationStatus.OK
    assert result.value > published
    assert result.value.weekday() not in (4, 5), "must not land on the BD weekend"


def test_bengali_calendar_month_refuses_rather_than_guesses() -> None:
    """The Bengali-to-Gregorian mapping shifts by year and reckoning.

    Guessing produces a confidently wrong deadline; refusing is correct.
    """
    result = parse("১৫ আশ্বিন ১৪৩৩")
    assert result.status is NormalizationStatus.RELATIVE_UNRESOLVED
    assert result.reason == "bengali_calendar_month"


def test_far_future_date_is_implausible() -> None:
    """Catches a reference number or act citation misread as a year."""
    result = parse("15 September 2099")
    assert result.status is NormalizationStatus.IMPLAUSIBLE
    assert result.is_usable is False


def test_long_past_date_is_implausible() -> None:
    assert parse("15 September 1998").status is NormalizationStatus.IMPLAUSIBLE


@pytest.mark.parametrize("text", ["", "   ", "see the notice board", "Ref: 2026/ADM/17-B"])
def test_non_dates_fail_cleanly(text: str) -> None:
    result = parse(text)
    assert result.is_usable is False
    assert result.value is None or result.status is not NormalizationStatus.OK


def test_raw_text_is_always_preserved() -> None:
    """The UI shows what the document said, so a user can correct a misparse."""
    for text in ("15 September 2026", "১৫ আশ্বিন ১৪৩৩", "garbage"):
        assert parse(text).raw_text == text


def test_confidence_tracks_status() -> None:
    assert parse("15 September 2026").confidence == 1.0
    assert parse("05/09/2026").confidence == 0.5
    assert parse("garbage").confidence == 0.0


# --------------------------------------------------------------------------
# bangla helpers
# --------------------------------------------------------------------------


def test_month_transliteration() -> None:
    assert transliterate_months("১৫ সেপ্টেম্বর ২০২৬") == "১৫ September ২০২৬"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Application deadline is 15 September", "en"),
        ("আবেদনের শেষ তারিখ ১৫ সেপ্টেম্বর", "bn"),
        ("Deadline: আবেদনের শেষ তারিখ 15 September 2026 submission", "mixed"),
        ("", "unknown"),
    ],
)
def test_language_detection(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_bengali_char_ratio_ignores_digits_and_punctuation() -> None:
    assert bengali_char_ratio("১৫ ২০২৬ ...") == 0.0


def test_deadline_marker_detection() -> None:
    assert has_deadline_marker("আবেদনের শেষ তারিখ ১৫ সেপ্টেম্বর")
    assert not has_deadline_marker("বিশ্ববিদ্যালয় ভর্তি বিজ্ঞপ্তি")
