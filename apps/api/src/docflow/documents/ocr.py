"""Tiered OCR.

Tier 1 is the native text layer (documents/pdf.py). This module is tier 2:
Tesseract with `eng+ben`, chosen because it ships in the worker image, needs no
GPU, and — decisively — returns **word-level bounding boxes**.

That last point is the whole design constraint. Vision-model OCR reads messy
scans better than any traditional engine and returns text with no coordinates,
which would silently destroy the highlight feature. So it stays a tier-3
fallback whose pages degrade honestly to page-level citations rather than
becoming the default.

If no Tesseract binary is present the pipeline continues without OCR: those
pages carry no text and the UI shows what it actually has. Failing the whole
upload because an optional engine is missing would be worse.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from functools import lru_cache

from docflow.ai.alignment.text_map import TextMap, Word
from docflow.ai.normalization.text import garbage_ratio
from docflow.core.logging import get_logger
from docflow.models.enums import TextExtractionMethod

logger = get_logger(__name__)

# Quality gate for tier 2. Below this the page escalates (Phase 2) rather than
# poisoning extraction with noise.
MIN_MEAN_CONFIDENCE = 55.0
MAX_GARBAGE_RATIO = 0.30

LANGUAGES = "eng+ben"


@dataclass(slots=True)
class OcrResult:
    text: str
    text_map: TextMap
    confidence: float
    method: TextExtractionMethod

    @property
    def passes_quality_gate(self) -> bool:
        return (
            self.confidence >= MIN_MEAN_CONFIDENCE and garbage_ratio(self.text) <= MAX_GARBAGE_RATIO
        )


@lru_cache(maxsize=1)
def is_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def engine_version() -> str:
    """Part of the OCR stage's idempotency key: upgrading Tesseract should
    re-run OCR, not silently reuse worse output."""
    if not is_available():
        return "none"
    try:
        import pytesseract

        return f"tesseract-{pytesseract.get_tesseract_version()}"
    except Exception:
        return "tesseract-unknown"


@lru_cache(maxsize=1)
def available_languages() -> tuple[str, ...]:
    try:
        import pytesseract

        return tuple(pytesseract.get_languages(config=""))
    except Exception:
        return ()


def _language_string() -> str:
    """Ask for Bengali only if the traineddata is installed.

    Requesting a missing language makes Tesseract fail the whole page rather
    than fall back, which would turn a partial capability into none.
    """
    installed = available_languages()
    if not installed:
        return "eng"
    wanted = [lang for lang in ("eng", "ben") if lang in installed]
    return "+".join(wanted) or "eng"


def recognize(image_bytes: bytes, page_number: int) -> OcrResult | None:
    """Run tier-2 OCR on a rendered page image.

    Returns word boxes normalised to [0,1] against the image, which is the same
    coordinate space the native text layer uses — so the citation path is
    identical whether a page was parsed or recognised.
    """
    if not is_available():
        return None

    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception:
        logger.warning("ocr.image_unreadable", page=page_number)
        return None

    width, height = image.size
    if not width or not height:
        return None

    try:
        data = pytesseract.image_to_data(
            image,
            lang=_language_string(),
            output_type=pytesseract.Output.DICT,
            config="--psm 3",
        )
    except Exception as exc:
        logger.warning("ocr.failed", page=page_number, error=type(exc).__name__)
        return None

    words: list[Word] = []
    text_parts: list[str] = []
    confidences: list[float] = []
    offset = 0
    previous_line: tuple[int, int, int] | None = None
    line_counter = -1

    for i, token in enumerate(data.get("text", [])):
        token = (token or "").strip()
        if not token:
            continue
        try:
            confidence = float(data["conf"][i])
        except (ValueError, TypeError):
            confidence = -1.0
        if confidence < 0:
            continue

        line_key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if line_key != previous_line:
            line_counter += 1
            if previous_line is not None:
                text_parts.append("\n")
                offset += 1
            previous_line = line_key
        elif text_parts:
            text_parts.append(" ")
            offset += 1

        x, y = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]
        words.append(
            Word(
                char_start=offset,
                char_end=offset + len(token),
                x0=max(0.0, min(1.0, x / width)),
                y0=max(0.0, min(1.0, y / height)),
                x1=max(0.0, min(1.0, (x + w) / width)),
                y1=max(0.0, min(1.0, (y + h) / height)),
                line_id=line_counter,
            )
        )
        text_parts.append(token)
        offset += len(token)
        confidences.append(confidence)

    text = "".join(text_parts)
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    result = OcrResult(
        text=text,
        text_map=TextMap(page_number=page_number, text=text, words=words),
        confidence=mean_confidence,
        method=TextExtractionMethod.OCR_TESSERACT,
    )

    if not result.passes_quality_gate:
        logger.info(
            "ocr.quality_gate_failed",
            page=page_number,
            confidence=round(mean_confidence, 1),
            garbage=round(garbage_ratio(text), 3),
        )
        # Still returned: partial text beats none, and the low confidence flows
        # into the fact confidence composite where it belongs.

    return result
