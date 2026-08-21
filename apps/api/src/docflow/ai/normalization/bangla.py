"""Bangla-specific normalisation.

The wedge is Bangladeshi student and institutional documents, and they mix scripts
constantly: a Bengali sentence with an English month, a Bengali date with ASCII
digits, an English paragraph with Bengali numerals. Handling that is the localised
correctness moat — general document AI tools do it badly.
"""

from __future__ import annotations

import re

# Gregorian months as written in Bengali, longest first so "সেপ্টেম্বর" is not
# shadowed by a shorter prefix during replacement.
BENGALI_MONTHS: dict[str, str] = {
    "জানুয়ারি": "January",
    "জানুয়ারী": "January",
    "ফেব্রুয়ারি": "February",
    "ফেব্রুয়ারী": "February",
    "মার্চ": "March",
    "এপ্রিল": "April",
    "মে": "May",
    "জুন": "June",
    "জুলাই": "July",
    "আগস্ট": "August",
    "অগাস্ট": "August",
    "সেপ্টেম্বর": "September",
    "সেপ্টেম্বার": "September",
    "অক্টোবর": "October",
    "অক্টোবার": "October",
    "নভেম্বর": "November",
    "নভেম্বার": "November",
    "ডিসেম্বর": "December",
    "ডিসেম্বার": "December",
}

# Bengali-calendar months. We deliberately do NOT convert these: the mapping to
# Gregorian shifts by year and by which reckoning (Bangladesh revised its calendar
# in 2019). Guessing would produce a confidently wrong deadline, which is worse
# than admitting we cannot resolve it — so these mark the date unresolved.
BENGALI_CALENDAR_MONTHS = frozenset(
    {
        "বৈশাখ",
        "জ্যৈষ্ঠ",
        "আষাঢ়",
        "শ্রাবণ",
        "ভাদ্র",
        "আশ্বিন",
        "কার্তিক",
        "অগ্রহায়ণ",
        "পৌষ",
        "মাঘ",
        "ফাল্গুন",
        "চৈত্র",
    }
)

# Words that mean "deadline" / "last date" in Bengali, used to spot which of
# several dates on a page is the primary one.
DEADLINE_MARKERS = frozenset({"শেষ তারিখ", "সময়সীমা", "শেষ সময়", "আবেদনের শেষ", "সর্বশেষ তারিখ"})

_MONTH_PATTERN = re.compile(
    "|".join(sorted((re.escape(m) for m in BENGALI_MONTHS), key=len, reverse=True))
)


def transliterate_months(text: str) -> str:
    """Replace Bengali Gregorian month names with their English equivalents."""
    return _MONTH_PATTERN.sub(lambda m: BENGALI_MONTHS[m.group(0)], text)


def has_bengali_calendar_month(text: str) -> bool:
    return any(month in text for month in BENGALI_CALENDAR_MONTHS)


def has_deadline_marker(text: str) -> bool:
    return any(marker in text for marker in DEADLINE_MARKERS)


def bengali_char_ratio(text: str) -> float:
    """Fraction of letters in the Bengali block. Drives language detection."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    bengali = sum(1 for ch in letters if "ঀ" <= ch <= "৿")
    return bengali / len(letters)


def detect_language(text: str) -> str:
    """Cheap, deterministic language detection.

    A model call for this would be slower, cost money, and be less reliable than
    counting codepoints.
    """
    ratio = bengali_char_ratio(text)
    if ratio >= 0.65:
        return "bn"
    if ratio >= 0.12:
        return "mixed"
    if not text.strip():
        return "unknown"
    return "en"
