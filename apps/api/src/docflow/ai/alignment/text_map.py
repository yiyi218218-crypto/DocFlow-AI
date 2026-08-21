"""The page text map: the bridge between characters and pixels.

For every page we store the full text plus, for every word, the character span it
occupies in that text and its bounding box in *rotation-normalised, [0,1]-relative*
display space.

Two decisions matter here and both are easy to get wrong:

* Coordinates are normalised against the page's DISPLAY box (post-/Rotate), so the
  browser needs one multiplication by the rendered viewport and nothing else. Any
  absolute unit forces the frontend to know the backend's DPI assumptions.

* PyMuPDF's `page.rect` reflects /Rotate but `get_text("words")` boxes do not, so
  the rotation matrix must be applied server-side. Skip it and highlights on
  scanned landscape pages land at 90-degree offsets — invisible in development,
  glaring to a user.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any

# A word is stored compactly as a flat list so a 500-word page stays small in JSONB:
#   [char_start, char_end, x0, y0, x1, y1, line_id]
WordTuple = tuple[int, int, float, float, float, float, int]


@dataclass(frozen=True, slots=True)
class Word:
    char_start: int
    char_end: int
    x0: float
    y0: float
    x1: float
    y1: float
    line_id: int


@dataclass(frozen=True, slots=True)
class Rect:
    """A highlight rectangle, normalised to [0,1] against the page display box."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "x0": round(self.x0, 5),
            "y0": round(self.y0, 5),
            "x1": round(self.x1, 5),
            "y1": round(self.y1, 5),
        }

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)


class TextMap:
    """Word-level index over one page's text."""

    __slots__ = ("_starts", "page_number", "text", "words")

    def __init__(self, page_number: int, text: str, words: list[Word]) -> None:
        self.page_number = page_number
        self.text = text
        self.words = sorted(words, key=lambda w: w.char_start)
        self._starts = [w.char_start for w in self.words]

    # --- serialisation ----------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "words": [
                [
                    w.char_start,
                    w.char_end,
                    round(w.x0, 5),
                    round(w.y0, 5),
                    round(w.x1, 5),
                    round(w.y1, 5),
                    w.line_id,
                ]
                for w in self.words
            ]
        }

    @classmethod
    def from_json(cls, page_number: int, text: str, payload: dict[str, Any] | None) -> TextMap:
        raw = (payload or {}).get("words") or []
        words = [
            Word(
                char_start=int(w[0]),
                char_end=int(w[1]),
                x0=float(w[2]),
                y0=float(w[3]),
                x1=float(w[4]),
                y1=float(w[5]),
                line_id=int(w[6]) if len(w) > 6 else 0,
            )
            for w in raw
        ]
        return cls(page_number=page_number, text=text, words=words)

    # --- lookup -----------------------------------------------------------

    def words_in_span(self, char_start: int, char_end: int) -> list[Word]:
        """Every word overlapping the half-open span [char_start, char_end).

        Binary search rather than a linear scan: extraction produces hundreds of
        facts per document and each one hits this.
        """
        if char_end <= char_start or not self.words:
            return []

        # First candidate: the last word starting at or before char_start, since
        # it may still extend into the span.
        lo = max(0, bisect_right(self._starts, char_start) - 1)
        hi = bisect_left(self._starts, char_end)

        return [
            w
            for w in self.words[lo : max(hi, lo + 1)]
            if w.char_start < char_end and w.char_end > char_start
        ]

    def rects_for_span(self, char_start: int, char_end: int, *, pad: float = 0.002) -> list[Rect]:
        """Merge the words in a span into one rectangle per visual line.

        One box per line rather than per word: a highlight that draws a separate
        rectangle around each word reads as a ransom note, not a highlight.
        """
        words = self.words_in_span(char_start, char_end)
        if not words:
            return []

        by_line: dict[int, list[Word]] = {}
        for w in words:
            by_line.setdefault(w.line_id, []).append(w)

        rects: list[Rect] = []
        for line_words in by_line.values():
            x0 = min(w.x0 for w in line_words)
            y0 = min(w.y0 for w in line_words)
            x1 = max(w.x1 for w in line_words)
            y1 = max(w.y1 for w in line_words)
            rects.append(
                Rect(
                    page=self.page_number,
                    x0=max(0.0, x0 - pad),
                    y0=max(0.0, y0 - pad),
                    x1=min(1.0, x1 + pad),
                    y1=min(1.0, y1 + pad),
                )
            )

        # Reading order: top to bottom, then left to right.
        rects.sort(key=lambda r: (round(r.y0, 3), r.x0))
        return rects

    @property
    def coverage(self) -> float:
        """Fraction of the page area covered by word boxes.

        Part of the OCR quality gate: a page whose text layer covers almost none
        of its visible area is a scan with a junk text layer, not a real one.
        """
        if not self.words:
            return 0.0
        return min(1.0, sum((w.x1 - w.x0) * (w.y1 - w.y0) for w in self.words))
