"""The eleven pipeline stages.

Kept in one cohesive module rather than eleven near-empty files: they share a
context object and a signature, and reading them top to bottom *is* the pipeline.

Every stage obeys the same contract:

  * It reads what it needs from Postgres and object storage, never from the
    previous stage's memory. That is what makes a crashed worker resumable
    rather than forced to restart.
  * It is idempotent under its key. Re-running a completed stage with an
    unchanged key is skipped by the orchestrator; re-running with a changed key
    (a new prompt version, say) recomputes only that stage.
  * It fails with a typed error code the UI can explain in plain language.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select

from docflow.ai.extraction.extractor import extract_document
from docflow.ai.normalization.bangla import detect_language
from docflow.ai.prompts.loader import prompt_version
from docflow.core import storage
from docflow.core.config import settings
from docflow.core.errors import CorruptDocument, InvalidFileType
from docflow.core.logging import get_logger
from docflow.documents import ocr
from docflow.documents.pdf import ParsedDocument, parse_pdf, render_page
from docflow.documents.validation import detect_mime, normalize_to_pdf
from docflow.models.document import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentFact,
    DocumentPage,
)
from docflow.models.enums import (
    DocumentStatus,
    Language,
    PipelineStage,
    TextExtractionMethod,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class StageContext:
    document: Document
    session: Any  # AsyncSession
    # Cached within a single worker run purely to avoid re-downloading and
    # re-parsing the same PDF eleven times. Never relied on for correctness:
    # every stage can rebuild it from storage.
    _canonical: bytes | None = None
    _parsed: ParsedDocument | None = None
    scratch: dict[str, Any] = field(default_factory=dict)

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.document.workspace_id

    @property
    def document_id(self) -> uuid.UUID:
        return self.document.id

    async def canonical_pdf(self) -> bytes:
        if self._canonical is None:
            key = self.document.storage_key_canonical
            if not key:
                raise CorruptDocument("The document has not been normalised yet.")
            self._canonical = await storage.get_object(key)
        return self._canonical

    async def parsed(self) -> ParsedDocument:
        if self._parsed is None:
            data = await self.canonical_pdf()
            self._parsed = await asyncio.to_thread(
                parse_pdf, data, max_pages=settings.max_page_count
            )
        return self._parsed


# --------------------------------------------------------------------------
# idempotency keys
# --------------------------------------------------------------------------


def stage_key(stage: PipelineStage, ctx: StageContext) -> str:
    """What must change for a stage to be worth re-running.

    Content hash for everything, plus the inputs that actually affect that
    stage's output — so bumping the extraction prompt re-runs extraction and
    nothing else, which makes backfills effectively free.
    """
    sha = ctx.document.content_sha256[:16]
    match stage:
        case PipelineStage.EXTRACTING:
            return f"{sha}:{prompt_version('extraction/v1_core.md')}"
        case PipelineStage.EMBEDDING:
            return f"{sha}:{settings.embedding_model}:{settings.embedding_dimensions}"
        case PipelineStage.CHUNKING:
            return f"{sha}:{settings.chunk_target_tokens}:{settings.chunk_overlap_ratio}"
        case PipelineStage.OCR:
            return f"{sha}:{ocr.engine_version()}"
        case _:
            return sha


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------


async def run_validating(ctx: StageContext) -> dict[str, Any]:
    """Magic bytes, size, and promotion out of quarantine.

    The declared Content-Type is advisory; only the bytes are evidence (T1).
    """
    data = await storage.get_object(ctx.document.storage_key_original)

    if len(data) > settings.max_upload_bytes:
        raise InvalidFileType("File exceeds the size limit.")

    detected = detect_mime(data, filename=ctx.document.original_filename)
    if detected not in settings.allowed_mime_types:
        raise InvalidFileType(
            f"{detected or 'This file type'} is not supported. "
            "Upload a PDF, Word document, text file or image."
        )

    ctx.document.mime_type = detected
    ctx.scratch["original_bytes"] = data
    return {"mime_type": detected, "size_bytes": len(data)}


async def run_normalizing(ctx: StageContext) -> dict[str, Any]:
    """Every input becomes a canonical PDF plus one rendered image per page.

    This is the decision that gives the frontend exactly one viewer and one
    highlight implementation instead of one per format.
    """
    data = ctx.scratch.get("original_bytes") or await storage.get_object(
        ctx.document.storage_key_original
    )
    canonical = await asyncio.to_thread(normalize_to_pdf, data, ctx.document.mime_type)

    key = storage.document_key(ctx.workspace_id, ctx.document_id, "canonical.pdf")
    await storage.put_object(key, canonical, content_type="application/pdf")
    ctx.document.storage_key_canonical = key
    ctx._canonical = canonical

    parsed = await ctx.parsed()
    ctx.document.page_count = parsed.page_count

    # Page renders: the viewer's instant path and the OCR tiers' input.
    async def render_and_store(page_number: int) -> None:
        image = await asyncio.to_thread(
            render_page, canonical, page_number, dpi=settings.page_render_dpi
        )
        await storage.put_object(
            storage.page_image_key(ctx.workspace_id, ctx.document_id, page_number),
            image,
            content_type="image/webp",
        )

    await asyncio.gather(*(render_and_store(p.page_number) for p in parsed.pages))
    return {"page_count": parsed.page_count, "canonical_key": key}


async def run_parsing(ctx: StageContext) -> dict[str, Any]:
    """Persist per-page text and the word-level text map."""
    parsed = await ctx.parsed()

    # Idempotent: a re-run replaces rather than duplicates.
    await ctx.session.execute(
        delete(DocumentPage).where(DocumentPage.document_id == ctx.document_id)
    )

    for page in parsed.pages:
        native = page.has_usable_text_layer
        ctx.session.add(
            DocumentPage(
                workspace_id=ctx.workspace_id,
                document_id=ctx.document_id,
                page_number=page.page_number,
                text=page.text,
                text_map=page.text_map.to_json(),
                width=page.width,
                height=page.height,
                rotation=page.rotation,
                storage_key_image=storage.page_image_key(
                    ctx.workspace_id, ctx.document_id, page.page_number
                ),
                extraction_method=(
                    TextExtractionMethod.NATIVE if native else TextExtractionMethod.NONE
                ),
                citation_granularity="span",
            )
        )
    await ctx.session.flush()
    return {
        "pages": parsed.page_count,
        "needs_ocr": parsed.scanned_page_numbers,
    }


async def run_ocr(ctx: StageContext) -> dict[str, Any]:
    """Tiered OCR, only on pages whose text layer is unusable."""
    parsed = await ctx.parsed()
    targets = parsed.scanned_page_numbers
    if not targets:
        return {"skipped": True, "reason": "all pages have a usable text layer"}

    if not ocr.is_available():
        # Honest degradation: the pipeline continues, those pages carry no text,
        # and the UI shows page-level citations rather than fabricated spans.
        logger.warning("ocr.unavailable", pages=len(targets))
        return {"skipped": True, "reason": "no OCR engine installed", "pages": targets}

    canonical = await ctx.canonical_pdf()
    updated = 0

    for page_number in targets:
        image = await storage.get_object(
            storage.page_image_key(ctx.workspace_id, ctx.document_id, page_number)
        )
        result = await asyncio.to_thread(ocr.recognize, image, page_number)
        if result is None:
            continue

        row = await ctx.session.scalar(
            select(DocumentPage).where(
                DocumentPage.document_id == ctx.document_id,
                DocumentPage.page_number == page_number,
            )
        )
        if row is None:
            continue
        row.text = result.text
        row.text_map = result.text_map.to_json()
        row.extraction_method = result.method
        row.ocr_confidence = result.confidence
        row.citation_granularity = "span" if result.text_map.words else "page"
        updated += 1

        # Keep the in-memory parse consistent so extraction sees OCR'd text.
        for page in parsed.pages:
            if page.page_number == page_number:
                page.text = result.text
                page.text_map = result.text_map

    await ctx.session.flush()
    _ = canonical
    return {"ocr_pages": updated, "attempted": len(targets)}


async def run_segmenting(ctx: StageContext) -> dict[str, Any]:
    """Language detection. Heading/section detection is Phase 3."""
    parsed = await ctx.parsed()
    detected = detect_language(parsed.full_text)
    ctx.document.language = Language(detected)
    return {"language": detected}


async def run_classifying(ctx: StageContext) -> dict[str, Any]:
    """Deliberately folded into extraction for the MVP.

    The extraction schema already returns document_type and language, so a
    separate classification call would cost money to produce a field we get for
    free. The stage stays in the machine because routing a type-specific prompt
    *before* extraction is a Phase 3 change, and keeping the slot means that is a
    stage implementation rather than a migration.
    """
    return {"skipped": True, "reason": "folded into extraction for MVP"}


async def run_extracting(ctx: StageContext) -> dict[str, Any]:
    """Extract, align and score facts, then persist them."""
    parsed = await ctx.parsed()

    rows = (
        (
            await ctx.session.execute(
                select(DocumentPage).where(DocumentPage.document_id == ctx.document_id)
            )
        )
        .scalars()
        .all()
    )
    page_methods = {r.page_number: r.extraction_method for r in rows}
    page_confidence = {
        r.page_number: r.ocr_confidence for r in rows if r.ocr_confidence is not None
    }

    result = await extract_document(
        parsed,
        page_methods=page_methods,
        page_ocr_confidence=page_confidence,
        workspace_id=ctx.workspace_id,
        document_id=ctx.document_id,
    )

    await ctx.session.execute(
        delete(DocumentFact).where(DocumentFact.document_id == ctx.document_id)
    )

    for fact in result.facts:
        ctx.session.add(
            DocumentFact(
                workspace_id=ctx.workspace_id,
                document_id=ctx.document_id,
                kind=fact.kind,
                payload=fact.payload,
                source_page=fact.source_page,
                source_quote=fact.source_quote,
                source_char_start=fact.alignment.char_start,
                source_char_end=fact.alignment.char_end,
                source_rects=[r.to_dict() for r in fact.alignment.rects],
                citation_status=fact.citation_status,
                citation_match_score=fact.alignment.score,
                confidence_raw=fact.confidence.raw,
                confidence_calibrated=fact.confidence.calibrated,
                confidence_bucket=fact.confidence.bucket,
                model_id=result.model_id,
                prompt_version=result.prompt_version,
                due_at=fact.due_at,
                normalization_status=(
                    fact.normalized_date.status.value if fact.normalized_date else None
                ),
                is_primary=bool(fact.payload.get("is_primary")),
            )
        )

    doc = ctx.document
    doc.title = result.title or doc.title or doc.original_filename
    doc.summary = result.summary
    doc.organization = result.organization
    doc.reference_number = result.reference_number
    doc.published_date = result.published_date
    doc.document_type = result.document_type
    if result.language != Language.UNKNOWN:
        doc.language = result.language
    doc.security_flags = result.security_flags
    doc.total_cost_usd = float(doc.total_cost_usd or 0) + result.total_cost_usd

    await ctx.session.flush()
    return {
        "facts": len(result.facts),
        "verified_ratio": round(result.verified_ratio, 3),
        "windows_failed": result.windows_failed,
        "cost_usd": round(result.total_cost_usd, 6),
    }


async def run_aligning(ctx: StageContext) -> dict[str, Any]:
    """Record the document-level citation verification rate.

    Alignment itself happens inside extraction, where the text maps are already
    in memory. This stage exists to compute and expose the flagship quality
    metric, and as the natural place to re-align if OCR later improves a page.
    """
    from docflow.models.enums import CitationStatus

    facts = (
        (
            await ctx.session.execute(
                select(DocumentFact).where(DocumentFact.document_id == ctx.document_id)
            )
        )
        .scalars()
        .all()
    )

    if not facts:
        return {"facts": 0, "verified_ratio": None}

    verified = sum(1 for f in facts if f.citation_status is CitationStatus.VERIFIED)
    return {
        "facts": len(facts),
        "verified_ratio": round(verified / len(facts), 3),
        "unverified": sum(1 for f in facts if f.citation_status is CitationStatus.UNVERIFIED),
    }


async def run_chunking(ctx: StageContext) -> dict[str, Any]:
    """Page-aware chunking. Chunks never silently cross a page boundary, so a
    chat citation resolves to rectangles through the same alignment path."""
    from docflow.ai.embeddings.chunker import chunk_pages

    parsed = await ctx.parsed()
    await ctx.session.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == ctx.document_id)
    )

    chunks = chunk_pages(
        parsed.pages,
        target_tokens=settings.chunk_target_tokens,
        overlap_ratio=settings.chunk_overlap_ratio,
    )
    for chunk in chunks:
        ctx.session.add(
            DocumentChunk(
                workspace_id=ctx.workspace_id,
                document_id=ctx.document_id,
                chunk_index=chunk.index,
                content=chunk.content,
                content_sha256=chunk.content_sha256,
                token_count=chunk.token_count,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                section_path=chunk.section_path,
            )
        )
    await ctx.session.flush()
    return {"chunks": len(chunks)}


async def run_embedding(ctx: StageContext) -> dict[str, Any]:
    """Embed chunks, using the global content-hash cache."""
    from docflow.ai.embeddings.embedder import embed_chunks

    chunks = (
        (
            await ctx.session.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == ctx.document_id)
            )
        )
        .scalars()
        .all()
    )

    if not chunks:
        return {"skipped": True, "reason": "no chunks"}

    await ctx.session.execute(
        delete(ChunkEmbedding).where(
            ChunkEmbedding.chunk_id.in_([c.id for c in chunks]),
        )
    )
    stats = await embed_chunks(chunks, ctx.session, workspace_id=ctx.workspace_id)
    await ctx.session.flush()
    return stats


async def run_indexing(ctx: StageContext) -> dict[str, Any]:
    """Terminal stage. Postgres maintains the FTS and HNSW indexes on write, so
    there is nothing to build — this marks the document ready."""
    ctx.document.status = DocumentStatus.COMPLETED
    return {"status": "completed"}


STAGE_RUNNERS = {
    PipelineStage.VALIDATING: run_validating,
    PipelineStage.NORMALIZING: run_normalizing,
    PipelineStage.PARSING: run_parsing,
    PipelineStage.OCR: run_ocr,
    PipelineStage.SEGMENTING: run_segmenting,
    PipelineStage.CLASSIFYING: run_classifying,
    PipelineStage.EXTRACTING: run_extracting,
    PipelineStage.ALIGNING: run_aligning,
    PipelineStage.CHUNKING: run_chunking,
    PipelineStage.EMBEDDING: run_embedding,
    PipelineStage.INDEXING: run_indexing,
}
