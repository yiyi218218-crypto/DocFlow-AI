"""Span alignment — the highest-value unit tests in the repo.

If these pass, the product's differentiator works. If they regress, every citation
in the app is silently wrong, which is worse than having no citations at all.
"""

from __future__ import annotations

import pytest

from docflow.ai.alignment.span_matcher import CitationStatus, align_quote, quote_similarity
from docflow.ai.alignment.text_map import TextMap, Word
from docflow.ai.normalization.text import garbage_ratio, normalize

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

CHAR_W = 0.012
LINE_H = 0.030


def build_text_map(page: int, text: str, *, with_boxes: bool = True) -> TextMap:
    """Lay text out on a synthetic page: one line per '\\n', monospaced words."""
    words: list[Word] = []
    offset = 0
    for line_id, line in enumerate(text.split("\n")):
        col = 0
        for token in line.split(" "):
            if token:
                words.append(
                    Word(
                        char_start=offset,
                        char_end=offset + len(token),
                        x0=0.05 + col * CHAR_W,
                        y0=0.05 + line_id * LINE_H,
                        x1=0.05 + (col + len(token)) * CHAR_W,
                        y1=0.05 + line_id * LINE_H + 0.020,
                        line_id=line_id,
                    )
                )
            col += len(token) + 1
            offset += len(token) + 1
    return TextMap(page_number=page, text=text, words=words if with_boxes else [])


CIRCULAR_P3 = (
    "Application Procedure\n"
    "Candidates must submit the completed form by 15 September 2026.\n"
    "Required documents include an academic transcript, a copy of the\n"
    "National ID card, and two passport-size photographs.\n"
    "Late submissions will not be considered under any circumstances."
)

CIRCULAR_P1 = (
    "University of Dhaka\n"
    "Office of the Registrar\n"
    "Admission Circular 2026-2027\n"
    "Published on 1 August 2026."
)


@pytest.fixture
def doc() -> dict[int, TextMap]:
    return {1: build_text_map(1, CIRCULAR_P1), 3: build_text_map(3, CIRCULAR_P3)}


# --------------------------------------------------------------------------
# normalisation offset map
# --------------------------------------------------------------------------


def test_offset_map_survives_whitespace_collapse() -> None:
    raw = "Submit    by\n\n15  September"
    n = normalize(raw)
    assert n.text == "submit by 15 september"
    start = n.text.index("15")
    s, e = n.to_source_span(start, start + 2)
    assert raw[s:e] == "15"


def test_offset_map_survives_bangla_digit_folding() -> None:
    raw = "আবেদনের শেষ তারিখ ১৫ সেপ্টেম্বর ২০২৬"
    n = normalize(raw)
    assert "15" in n.text
    assert "2026" in n.text
    start = n.text.index("15")
    s, e = n.to_source_span(start, start + 2)
    assert raw[s:e] == "১৫"


def test_invisible_characters_are_dropped() -> None:
    # Zero-width joiners are a classic injection hiding place as well as noise.
    raw = "dead​line‍ 2026"
    assert normalize(raw).text == "deadline 2026"


def test_garbage_ratio_flags_ocr_noise() -> None:
    assert garbage_ratio("clean readable text") == 0.0
    assert garbage_ratio("���������") > 0.9


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------


def test_exact_quote_is_verified_with_rects(doc) -> None:
    result = align_quote(
        "Candidates must submit the completed form by 15 September 2026.",
        doc,
        claimed_page=3,
    )
    assert result.status is CitationStatus.VERIFIED
    assert result.score >= 95
    assert result.page == 3
    assert result.rects, "a verified citation must produce a highlight"
    assert all(0.0 <= r.x0 < r.x1 <= 1.0 for r in result.rects)
    assert all(0.0 <= r.y0 < r.y1 <= 1.0 for r in result.rects)


def test_whitespace_and_case_variance_still_verifies(doc) -> None:
    """Models reflow whitespace constantly. Exact matching would reject this."""
    result = align_quote(
        "candidates  must submit   the completed FORM by 15 september 2026",
        doc,
        claimed_page=3,
    )
    assert result.status is CitationStatus.VERIFIED


def test_truncated_quote_still_locates(doc) -> None:
    result = align_quote("submit the completed form by 15 September", doc, claimed_page=3)
    assert result.status in (CitationStatus.VERIFIED, CitationStatus.PARTIAL)
    assert result.matched_text is not None
    assert "completed form" in result.matched_text.lower()


def test_fabricated_quote_is_rejected(doc) -> None:
    """The anti-hallucination gate. This is the test that matters most."""
    result = align_quote(
        "Applicants receive a guaranteed full tuition waiver and a monthly stipend.",
        doc,
        claimed_page=3,
    )
    assert result.status is CitationStatus.UNVERIFIED
    assert result.score < 88


def test_wrong_claimed_page_is_recovered(doc) -> None:
    """Models get the page number wrong; that must not lose the citation."""
    result = align_quote("Published on 1 August 2026.", doc, claimed_page=3)
    assert result.page == 1
    assert result.status is CitationStatus.VERIFIED


def test_too_short_quote_is_not_evidence(doc) -> None:
    # "2026" appears all over the document — matching it proves nothing.
    result = align_quote("2026", doc, claimed_page=3)
    assert result.status is CitationStatus.UNVERIFIED


def test_multiline_span_produces_one_rect_per_line(doc) -> None:
    """One box per visual line. Per-word boxes read as a ransom note."""
    result = align_quote(
        "Required documents include an academic transcript, a copy of the\n"
        "National ID card, and two passport-size photographs.",
        doc,
        claimed_page=3,
    )
    assert result.status is CitationStatus.VERIFIED
    assert len(result.rects) == 2
    assert result.rects[0].y0 < result.rects[1].y0  # reading order


def test_page_without_word_boxes_degrades_honestly() -> None:
    """VLM OCR returns text with no coordinates.

    The correct behaviour is page-level citation, never a fabricated highlight.
    """
    pages = {3: build_text_map(3, CIRCULAR_P3, with_boxes=False)}
    result = align_quote(
        "Candidates must submit the completed form by 15 September 2026.",
        pages,
        claimed_page=3,
    )
    assert result.status is CitationStatus.PAGE_ONLY
    assert result.page == 3
    assert result.rects == []


def test_search_stops_early_on_confident_hit(doc) -> None:
    """Claimed page first: a confident hit must not trigger a full-document scan."""
    result = align_quote(
        "Late submissions will not be considered under any circumstances.",
        doc,
        claimed_page=3,
    )
    assert result.status is CitationStatus.VERIFIED
    assert result.searched_pages == 1


def test_empty_and_missing_inputs_do_not_raise(doc) -> None:
    assert align_quote("", doc, claimed_page=1).status is CitationStatus.UNVERIFIED
    assert (
        align_quote("anything at all here", {}, claimed_page=1).status is CitationStatus.UNVERIFIED
    )


# --------------------------------------------------------------------------
# word lookup
# --------------------------------------------------------------------------


def test_words_in_span_is_inclusive_of_partial_overlap() -> None:
    tm = build_text_map(1, "alpha beta gamma delta")
    # Span covering the middle of "beta" through the middle of "gamma".
    words = tm.words_in_span(7, 13)
    assert [tm.text[w.char_start : w.char_end] for w in words] == ["beta", "gamma"]


def test_words_in_span_empty_for_gap() -> None:
    tm = build_text_map(1, "alpha beta")
    assert tm.words_in_span(5, 6) == []  # the space between words


def test_text_map_json_round_trip() -> None:
    tm = build_text_map(2, CIRCULAR_P3)
    restored = TextMap.from_json(2, CIRCULAR_P3, tm.to_json())
    assert len(restored.words) == len(tm.words)
    assert restored.words[0].char_start == tm.words[0].char_start
    assert restored.words[-1].line_id == tm.words[-1].line_id


# --------------------------------------------------------------------------
# similarity, used for cross-window de-duplication
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b", "expect_similar"),
    [
        ("Academic transcript", "academic  transcript", True),
        ("15 September 2026", "১৫ September ২০২৬", True),
        ("Academic transcript", "National ID card", False),
    ],
)
def test_quote_similarity(a: str, b: str, expect_similar: bool) -> None:
    score = quote_similarity(a, b)
    assert (score >= 90) is expect_similar
