"""Page-aware chunking.

Two constraints shape this:

* A chunk never silently spans a page boundary. Every chunk records the page and
  character offsets it came from, so a chat citation resolves to a rectangle
  through the *same* alignment path extraction uses. One citation implementation,
  two consumers.

* Section context is prepended to the embedded text. That is a free ~80%
  substitute for LLM-generated contextual headers, which cost one model call per
  chunk and belong in Phase 3 behind a measurement.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from docflow.ai.llm.pricing import estimate_tokens
from docflow.documents.pdf import ParsedPage

# A line that looks like a heading: short, and either title-case, all-caps or
# numbered. Font-size-based detection is Phase 3; this covers circulars well.
_HEADING = re.compile(
    r"^\s*(?:\d+[.)]\s+)?(?P<text>[A-Z][A-Za-z0-9 ,&/()'-]{2,60}|[ঀ-৿ ,&/()'-]{3,60})\s*:?\s*$"
)


@dataclass(slots=True)
class Chunk:
    index: int
    content: str
    content_sha256: str
    token_count: int
    page_start: int
    page_end: int
    char_start: int
    char_end: int
    section_path: str | None = None


def detect_section(lines: list[str], upto: int) -> str | None:
    """Nearest preceding heading, used as the chunk's section path."""
    for i in range(min(upto, len(lines)) - 1, -1, -1):
        line = lines[i].strip()
        if not line or len(line) > 70:
            continue
        match = _HEADING.match(line)
        if match and not line.endswith((".", "।")):
            return match.group("text").strip()
    return None


def chunk_pages(
    pages: list[ParsedPage],
    *,
    target_tokens: int = 700,
    overlap_ratio: float = 0.12,
) -> list[Chunk]:
    """Split a document into overlapping, page-scoped chunks."""
    chunks: list[Chunk] = []
    index = 0

    for page in pages:
        text = page.text
        if not text.strip():
            continue

        lines = text.split("\n")
        # Character budget derived from the token target. Estimating per line
        # would be more precise and much slower; the ratio is stable enough.
        chars_per_token = max(1.0, len(text) / max(1, estimate_tokens(text)))
        target_chars = int(target_tokens * chars_per_token)
        overlap_chars = int(target_chars * overlap_ratio)

        cursor = 0
        line_starts: list[int] = []
        position = 0
        for line in lines:
            line_starts.append(position)
            position += len(line) + 1

        while cursor < len(text):
            end = min(len(text), cursor + target_chars)

            # Prefer to break at a line boundary so a requirement list is not
            # cut mid-item.
            if end < len(text):
                newline = text.rfind("\n", cursor + target_chars // 2, end)
                if newline > cursor:
                    end = newline

            body = text[cursor:end].strip()
            if body:
                line_index = sum(1 for start in line_starts if start <= cursor)
                section = detect_section(lines, line_index)
                content = f"{section}\n{body}" if section else body

                chunks.append(
                    Chunk(
                        index=index,
                        content=content,
                        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
                        token_count=estimate_tokens(content),
                        page_start=page.page_number,
                        page_end=page.page_number,
                        char_start=cursor,
                        char_end=end,
                        section_path=section,
                    )
                )
                index += 1

            if end >= len(text):
                break
            cursor = max(cursor + 1, end - overlap_chars)

    return chunks
