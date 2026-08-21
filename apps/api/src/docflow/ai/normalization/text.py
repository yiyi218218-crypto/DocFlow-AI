"""Offset-preserving text normalisation.

Fuzzy matching a model's quote against page text only works if both sides are
normalised the same way — but a citation is useless unless we can map the match
back to *raw* character offsets, because that is what the word boxes are indexed by.

So normalisation is done character by character while recording, for every output
character, which input character produced it. Normalising with str.replace() and
then trying to recover offsets afterwards is where this goes wrong.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

# Bengali digits U+09E6–U+09EF, and the Eastern Arabic digits that appear in
# documents that mix scripts.
_DIGIT_FOLD = {
    **{chr(0x09E6 + i): str(i) for i in range(10)},  # ০-৯ Bengali
    **{chr(0x0660 + i): str(i) for i in range(10)},  # ٠-٩ Arabic-Indic
    **{chr(0x06F0 + i): str(i) for i in range(10)},  # ۰-۹ Extended Arabic-Indic
}

# Zero-width and directional marks. These are invisible, carry no meaning for
# matching, and are also a classic prompt-injection hiding place — dropping them
# is both a correctness and a security win.
_INVISIBLE = frozenset("​‌‍‎‏⁠﻿­‪‫‬‭‮")

_PUNCT_FOLD = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
    " ": " ",
    " ": " ",
    " ": " ",
}


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Normalised text plus an exact map back to the source."""

    text: str
    #  index_map[i] == offset in `source` of the character that produced text[i]
    index_map: tuple[int, ...]
    source_length: int

    def to_source_span(self, start: int, end: int) -> tuple[int, int]:
        """Map a half-open span in normalised space back to source space."""
        if not self.index_map or start >= end:
            return (0, 0)
        start = max(0, min(start, len(self.index_map) - 1))
        end = max(start + 1, min(end, len(self.index_map)))
        source_start = self.index_map[start]
        # +1 because index_map points at the first source char of the last
        # normalised char, and the span is half-open.
        source_end = self.index_map[end - 1] + 1
        return (source_start, min(source_end, self.source_length))


def normalize(source: str, *, fold_case: bool = True) -> NormalizedText:
    """Normalise for matching while preserving a source-offset map.

    NFKC per character (so ligatures and compatibility forms match), digits folded
    to ASCII, smart punctuation flattened, invisible marks dropped, whitespace runs
    collapsed to a single space, and leading/trailing space trimmed.
    """
    out: list[str] = []
    idx: list[int] = []
    pending_space_at: int | None = None

    for i, ch in enumerate(source):
        if ch in _INVISIBLE:
            continue

        if ch.isspace():
            # Remember where the run started; emit lazily so trailing space is free.
            if pending_space_at is None:
                pending_space_at = i
            continue

        if pending_space_at is not None:
            if out:  # never emit a leading space
                out.append(" ")
                idx.append(pending_space_at)
            pending_space_at = None

        ch = _PUNCT_FOLD.get(ch, ch)
        ch = _DIGIT_FOLD.get(ch, ch)

        decomposed = unicodedata.normalize("NFKC", ch)
        if fold_case:
            decomposed = decomposed.casefold()

        for out_ch in decomposed:
            if out_ch in _INVISIBLE:
                continue
            out.append(out_ch)
            idx.append(i)

    return NormalizedText(text="".join(out), index_map=tuple(idx), source_length=len(source))


def normalize_plain(source: str, *, fold_case: bool = True) -> str:
    """Normalise without building the offset map, for comparisons and hashing."""
    return normalize(source, fold_case=fold_case).text


def fold_digits(source: str) -> str:
    """Bengali / Arabic-Indic digits to ASCII, leaving everything else alone."""
    return "".join(_DIGIT_FOLD.get(ch, ch) for ch in source)


def strip_invisible(source: str) -> str:
    return "".join(ch for ch in source if ch not in _INVISIBLE)


def garbage_ratio(text: str) -> float:
    """Fraction of characters that look like OCR noise.

    Feeds the OCR quality gate: replacement characters, control codes and private
    use area codepoints are the signature of a failed recognition pass.
    """
    if not text:
        return 1.0
    bad = sum(
        1
        for ch in text
        if ch == "�" or (unicodedata.category(ch) in ("Cc", "Co", "Cn") and ch not in "\n\r\t")
    )
    return bad / len(text)
