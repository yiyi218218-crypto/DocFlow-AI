"""Fuzzy alignment of a model-produced quote to a source span.

This is the core algorithm of the product. Everything else is plumbing.

Why fuzzy and not exact: models normalise whitespace, expand abbreviations, drop
diacritics and truncate mid-word. Exact matching rejects roughly a third of quotes
that are genuinely correct, which would make the citation feature look broken.

Why this is also the anti-hallucination gate: a fabricated fact cannot produce a
quote that fuzzy-matches real page text at 95 or above. The verification rate is
therefore a fully automatic hallucination metric — no judge model, no labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from docflow.ai.alignment.text_map import Rect, TextMap
from docflow.ai.normalization.text import normalize

# Below this a "match" is coincidence, not evidence.
MIN_USEFUL_SCORE = 55.0


class CitationStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    PAGE_ONLY = "page_only"


@dataclass(slots=True)
class Alignment:
    status: CitationStatus
    score: float
    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    rects: list[Rect] = field(default_factory=list)
    matched_text: str | None = None
    searched_pages: int = 0

    @property
    def is_located(self) -> bool:
        return self.char_start is not None and self.char_end is not None

    @property
    def has_highlight(self) -> bool:
        return bool(self.rects)


def _best_window(needle: str, haystack: str) -> tuple[float, int, int]:
    """Best-matching window of `haystack` for `needle`, in normalised space.

    rapidfuzz's partial_ratio_alignment gives both the score and the window, which
    is what makes this a *locator* rather than just a similarity score.
    """
    if not needle or not haystack:
        return (0.0, 0, 0)

    result = fuzz.partial_ratio_alignment(needle, haystack)
    if result is None:
        return (0.0, 0, 0)

    start, end = result.dest_start, result.dest_end
    score = float(result.score)

    # partial_ratio anchors on the best substring but can under- or over-shoot the
    # ends. Nudge the window by a few characters and keep the best true ratio;
    # this measurably tightens highlight boundaries on real quotes.
    best = (score, start, end)
    span = end - start
    if span > 0:
        for ds in (-4, -2, 0, 2, 4):
            for de in (-4, -2, 0, 2, 4):
                s = max(0, start + ds)
                e = min(len(haystack), end + de)
                if e - s < span * 0.6 or e <= s:
                    continue
                candidate = fuzz.ratio(needle, haystack[s:e])
                if candidate > best[0]:
                    best = (float(candidate), s, e)
    return best


def align_quote(
    quote: str,
    text_maps: dict[int, TextMap],
    *,
    claimed_page: int | None = None,
    verified_threshold: float = 95.0,
    partial_threshold: float = 88.0,
    neighbour_radius: int = 2,
) -> Alignment:
    """Locate `quote` in the document and produce highlight rectangles.

    Search widens only as needed — the claimed page first, then its neighbours,
    then the whole document. Models get the page right most of the time, and
    searching every page for every fact is both slower and more likely to land on
    a coincidental match elsewhere.
    """
    quote = (quote or "").strip()
    if not quote or not text_maps:
        return Alignment(status=CitationStatus.UNVERIFIED, score=0.0)

    normalized_quote = normalize(quote).text
    if len(normalized_quote) < 8:
        # Too short to be evidence of anything: "2026" matches half the document.
        return Alignment(status=CitationStatus.UNVERIFIED, score=0.0)

    all_pages = sorted(text_maps)
    if claimed_page in text_maps:
        neighbours = [
            p for p in all_pages if p != claimed_page and abs(p - claimed_page) <= neighbour_radius
        ]
        remainder = [p for p in all_pages if p != claimed_page and p not in neighbours]
        tiers = [[claimed_page], neighbours, remainder]
    else:
        tiers = [all_pages]

    best = Alignment(status=CitationStatus.UNVERIFIED, score=0.0)
    searched = 0

    for tier in tiers:
        for page in tier:
            if not tier:
                continue
            tm = text_maps[page]
            searched += 1
            normalized_page = normalize(tm.text)
            score, ns, ne = _best_window(normalized_quote, normalized_page.text)
            if score <= best.score:
                continue

            raw_start, raw_end = normalized_page.to_source_span(ns, ne)
            best = Alignment(
                status=_classify(score, verified_threshold, partial_threshold),
                score=score,
                page=page,
                char_start=raw_start,
                char_end=raw_end,
                rects=tm.rects_for_span(raw_start, raw_end),
                matched_text=tm.text[raw_start:raw_end],
            )

        # Stop as soon as a tier yields a confident hit. A better match on a far
        # page is almost always a coincidence, not a correction.
        if best.score >= verified_threshold:
            break

    best.searched_pages = searched

    if best.score < MIN_USEFUL_SCORE:
        return Alignment(
            status=CitationStatus.UNVERIFIED, score=best.score, searched_pages=searched
        )

    # Located the text but the page has no word boxes (VLM OCR tier): navigate to
    # the page, do not fabricate a highlight.
    if best.is_located and not best.rects:
        best.status = (
            CitationStatus.PAGE_ONLY
            if best.score >= partial_threshold
            else CitationStatus.UNVERIFIED
        )

    return best


def _classify(score: float, verified: float, partial: float) -> CitationStatus:
    if score >= verified:
        return CitationStatus.VERIFIED
    if score >= partial:
        return CitationStatus.PARTIAL
    return CitationStatus.UNVERIFIED


def quote_similarity(a: str, b: str) -> float:
    """Normalised similarity, used for de-duplicating facts across windows."""
    return float(fuzz.token_set_ratio(normalize(a).text, normalize(b).text))


def edit_distance(a: str, b: str) -> int:
    return int(Levenshtein.distance(normalize(a).text, normalize(b).text))
