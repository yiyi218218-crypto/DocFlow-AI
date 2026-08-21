"""Upload validation and normalisation to the canonical PDF representation.

The declared Content-Type is a claim by the uploader; the bytes are the evidence.
Everything here sniffs magic bytes and ignores what the request said (T1).
"""

from __future__ import annotations

import filetype

from docflow.core.errors import InvalidFileType
from docflow.core.logging import get_logger
from docflow.documents.pdf import image_to_pdf, text_to_pdf

logger = get_logger(__name__)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIMES = {"text/plain", "text/markdown"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/tiff"}

# Extensions we accept when the bytes alone are ambiguous (plain text has no
# magic number). Everything else must be identified from content.
_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}


def detect_mime(data: bytes, *, filename: str | None = None) -> str:
    """Identify a file from its bytes.

    `filetype` reads magic numbers only. Plain text has none, so it is the one
    case where the filename is consulted — and then only after confirming the
    bytes actually decode as UTF-8 text.
    """
    if not data:
        raise InvalidFileType("The uploaded file is empty.")

    kind = filetype.guess(data)
    if kind is not None:
        mime = kind.mime
        # A DOCX is a ZIP; filetype reports the container, so confirm the
        # OOXML marker before trusting the extension.
        if mime == "application/zip" and _looks_like_docx(data):
            return DOCX_MIME
        return str(mime)

    if _is_probably_text(data):
        suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if filename and "." in filename else ""
        if suffix in _TEXT_EXTENSIONS or not suffix:
            return "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"

    raise InvalidFileType(
        "This file type could not be identified. Upload a PDF, Word document, text file or image."
    )


def _looks_like_docx(data: bytes) -> bool:
    # OOXML always carries these entries near the start of the archive.
    head = data[:4096]
    return b"word/" in head or b"[Content_Types].xml" in head


def _is_probably_text(data: bytes, *, sample: int = 8192) -> bool:
    chunk = data[:sample]
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def normalize_to_pdf(data: bytes, mime_type: str) -> bytes:
    """Convert any supported input into a PDF.

    One canonical representation is what gives the frontend a single viewer and
    a single highlight implementation. See docs/10-architecture.md §11.1.
    """
    if mime_type == PDF_MIME:
        return data

    if mime_type in IMAGE_MIMES:
        return image_to_pdf(data)

    if mime_type in TEXT_MIMES:
        return text_to_pdf(data.decode("utf-8", errors="replace"))

    if mime_type == DOCX_MIME:
        return _docx_to_pdf(data)

    raise InvalidFileType(f"Cannot convert {mime_type} to PDF.")


def _docx_to_pdf(data: bytes) -> bytes:
    """DOCX via LibreOffice headless, with a text-extraction fallback.

    LibreOffice is a ~450 MB layer and is not always present in a dev
    environment. Rather than failing the upload, fall back to extracting the
    document's text and rendering that: layout fidelity is lost, but the
    deadlines and requirements — which is what the product is actually for —
    survive, and the citation model still works.
    """
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.docx"
            source.write_bytes(data)
            try:
                subprocess.run(  # noqa: S603 - argv list, never a shell string
                    [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(source)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                output = Path(tmp) / "input.pdf"
                if output.exists():
                    return output.read_bytes()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                logger.warning("docx.libreoffice_failed", error=type(exc).__name__)

    logger.info("docx.text_fallback")
    return text_to_pdf(_extract_docx_text(data), title="Document")


def _extract_docx_text(data: bytes) -> str:
    """Pull paragraph text straight out of the OOXML, with no extra dependency."""
    import io
    import re
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise InvalidFileType("This Word document could not be read.") from exc

    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
