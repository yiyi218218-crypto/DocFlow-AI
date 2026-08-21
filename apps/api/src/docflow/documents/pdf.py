"""PDF parsing: text, word boxes and page rendering.

The one subtle thing in this file is rotation.

PyMuPDF's `page.rect` reflects the page's /Rotate entry, but the word boxes from
`get_text("words")` are in *unrotated* coordinates. pdf.js in the browser applies
/Rotate when it builds its viewport. So if we normalise raw word boxes against
`page.rect` without transforming them first, highlights on rotated pages — which
scanners produce constantly — land at 90-degree offsets.

The bug is invisible in development (most PDFs have /Rotate 0) and glaring to a
user, so `tests/unit/test_pdf_extraction.py` covers all four rotations against a
generated fixture.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pymupdf
from PIL import Image

from docflow.ai.alignment.text_map import TextMap, Word
from docflow.ai.normalization.text import garbage_ratio
from docflow.core.errors import CorruptDocument, EncryptedDocument, TooManyPages
from docflow.core.logging import get_logger

logger = get_logger(__name__)

# A page needs enough text, enough of the page covered by it, and not too much
# junk before we trust its text layer. All three matter: scanned PDFs often carry
# a thin junk text layer that passes a naive length check.
MIN_CHARS_FOR_NATIVE = 120
# Measured, not guessed. Word-box coverage of an A4 page:
#   blank 0.000 | one line 0.012 | four lines 0.025 | dense full page 0.309
# So the threshold only needs to separate "has real positioned words" from
# "degenerate/empty text layer". (An earlier draft of the design doc said 40%,
# which would have routed every real page to OCR.)
MIN_COVERAGE_FOR_NATIVE = 0.005
MAX_GARBAGE_RATIO = 0.15


@dataclass(slots=True)
class ParsedPage:
    page_number: int
    text: str
    text_map: TextMap
    width: float
    height: float
    rotation: int
    char_count: int
    coverage: float
    garbage_ratio: float

    @property
    def has_usable_text_layer(self) -> bool:
        """Whether the native text layer is good enough to skip OCR."""
        return (
            self.char_count >= MIN_CHARS_FOR_NATIVE
            and self.coverage >= MIN_COVERAGE_FOR_NATIVE
            and self.garbage_ratio < MAX_GARBAGE_RATIO
        )


@dataclass(slots=True)
class ParsedDocument:
    pages: list[ParsedPage]
    page_count: int
    is_encrypted: bool = False

    @property
    def scanned_page_numbers(self) -> list[int]:
        return [p.page_number for p in self.pages if not p.has_usable_text_layer]

    @property
    def is_scanned(self) -> bool:
        """More than a third of pages lacking a usable text layer reads as a scan."""
        if not self.pages:
            return True
        return len(self.scanned_page_numbers) / len(self.pages) > 0.33

    @property
    def full_text(self) -> str:
        return "\n\n".join(f"[page {p.page_number}]\n{p.text}" for p in self.pages)

    def text_maps(self) -> dict[int, TextMap]:
        return {p.page_number: p.text_map for p in self.pages}


def open_document(data: bytes) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise CorruptDocument("This PDF could not be read. It may be damaged.") from exc

    if doc.needs_pass:
        doc.close()
        raise EncryptedDocument(
            "This PDF is password-protected. Remove the password and upload it again."
        )
    return doc


def parse_pdf(data: bytes, *, max_pages: int = 150) -> ParsedDocument:
    """Extract text and word boxes for every page."""
    doc = open_document(data)
    try:
        if doc.page_count > max_pages:
            raise TooManyPages(
                f"This document has {doc.page_count} pages; the current limit is {max_pages}."
            )
        pages = [_parse_page(doc[i], i + 1) for i in range(doc.page_count)]
        return ParsedDocument(pages=pages, page_count=doc.page_count)
    finally:
        doc.close()


def _parse_page(page: pymupdf.Page, page_number: int) -> ParsedPage:
    # page.rect is the display box: it already accounts for /Rotate.
    display = page.rect
    width = float(display.width) or 1.0
    height = float(display.height) or 1.0
    rotation = int(page.rotation or 0)

    # Word boxes come back in unrotated space, so transform them into display
    # space before normalising. This is the whole rotation fix.
    matrix = page.rotation_matrix

    raw_words = page.get_text("words")  # (x0,y0,x1,y1,word,block_no,line_no,word_no)
    raw_words.sort(key=lambda w: (w[5], w[6], w[7]))  # reading order

    text_parts: list[str] = []
    words: list[Word] = []
    offset = 0
    previous_line: tuple[int, int] | None = None
    line_counter = -1

    for x0, y0, x1, y1, token, block_no, line_no, _word_no in raw_words:
        if not token:
            continue

        line_key = (block_no, line_no)
        if line_key != previous_line:
            line_counter += 1
            if previous_line is not None:
                text_parts.append("\n")
                offset += 1
            previous_line = line_key
        elif text_parts:
            text_parts.append(" ")
            offset += 1

        rect = pymupdf.Rect(x0, y0, x1, y1) * matrix
        words.append(
            Word(
                char_start=offset,
                char_end=offset + len(token),
                x0=max(0.0, min(1.0, rect.x0 / width)),
                y0=max(0.0, min(1.0, rect.y0 / height)),
                x1=max(0.0, min(1.0, rect.x1 / width)),
                y1=max(0.0, min(1.0, rect.y1 / height)),
                line_id=line_counter,
            )
        )
        text_parts.append(token)
        offset += len(token)

    text = "".join(text_parts)
    text_map = TextMap(page_number=page_number, text=text, words=words)

    return ParsedPage(
        page_number=page_number,
        text=text,
        text_map=text_map,
        width=width,
        height=height,
        rotation=rotation,
        char_count=len(text),
        coverage=text_map.coverage,
        garbage_ratio=garbage_ratio(text),
    )


def render_page(data: bytes, page_number: int, *, dpi: int = 150) -> bytes:
    """Render one page to WebP.

    Used for the viewer's fast path (shown while the canvas renders, and as the
    fallback when the canvas fails — the coordinates are identical either way)
    and as the input image for the OCR tiers.
    """
    doc = open_document(data)
    try:
        page = doc[page_number - 1]
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        # PyMuPDF cannot encode WebP, so go through Pillow. Worth it: WebP is
        # roughly half the size of PNG for these renders and the viewer fetches
        # one per page.
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=80, method=4)
        return buffer.getvalue()
    finally:
        doc.close()


def page_count(data: bytes) -> int:
    doc = open_document(data)
    try:
        return int(doc.page_count)
    finally:
        doc.close()


def image_to_pdf(image_bytes: bytes) -> bytes:
    """Wrap an image into a single-page PDF.

    Part of the canonical-representation decision: every input becomes a PDF so
    the viewer, the citation model and the highlight code have exactly one path
    instead of one per format.
    """
    try:
        doc = pymupdf.open()
        img = pymupdf.open(stream=image_bytes, filetype=None)
        pdf_bytes = img.convert_to_pdf()
        img.close()
        doc.close()
        return bytes(pdf_bytes)
    except Exception as exc:
        raise CorruptDocument("This image could not be read.") from exc


def text_to_pdf(text: str, *, title: str = "Document") -> bytes:
    """Render plain text to a PDF using a Bengali-capable font.

    Without a font that covers the Bengali block, Bangla .txt uploads render as
    tofu boxes and produce an empty text layer.
    """
    doc = pymupdf.open()
    writer = pymupdf.DocumentWriter(buffer := io.BytesIO())
    story = pymupdf.Story(
        html=f"<h3>{_escape(title)}</h3><pre>{_escape(text)}</pre>",
        user_css=(
            "* { font-family: sans-serif; font-size: 10pt; }"
            "pre { white-space: pre-wrap; word-wrap: break-word; }"
        ),
    )
    rect = pymupdf.paper_rect("a4")
    body = rect + (36, 36, -36, -36)

    more = True
    while more:
        device = writer.begin_page(rect)
        more, _ = story.place(body)
        story.draw(device)
        writer.end_page()
    writer.close()
    doc.close()
    return buffer.getvalue()


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
