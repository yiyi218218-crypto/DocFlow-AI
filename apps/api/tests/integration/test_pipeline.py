"""The pipeline, end to end, against real Postgres and real MinIO.

This is the test that says the product works. It uploads a PDF, runs all eleven
stages, and asserts the full provenance chain survived into the database:
document → page → char span → rectangle → fact → due date.

It also kills the pipeline mid-run and restarts it, because resumability is the
whole justification for keeping stage state in Postgres rather than in the queue.
"""

from __future__ import annotations

import uuid

import pymupdf
import pytest
from sqlalchemy import select

from docflow.core import storage
from docflow.core.database import tenant_session
from docflow.models.document import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentFact,
    DocumentPage,
    DocumentStage,
)
from docflow.models.enums import (
    CitationStatus,
    DocumentStatus,
    FactKind,
    PipelineStage,
    StageStatus,
)
from docflow.pipeline.orchestrator import ensure_stage_rows, process_document, reset_stages_from

pytestmark = pytest.mark.integration

CIRCULAR = """University of Dhaka
Office of the Registrar

Admission Circular 2026-2027

Applications must be submitted by 15 September 2026.
Required documents include an academic transcript.
Applicants must also provide a copy of the National ID card.
Two passport-size photographs are required with the application.
Late submissions will not be considered under any circumstances.
"""


def build_pdf(text: str = CIRCULAR) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(50, 60, 545, 780), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
async def uploaded(tenants, admin_session):  # type: ignore[no-untyped-def]
    """A document row plus its bytes in object storage, ready to process."""
    await storage.ensure_bucket()

    document_id = uuid.uuid4()
    workspace_id = tenants.ws_a
    pdf = build_pdf()

    key = storage.quarantine_key(workspace_id, document_id, "circular.pdf")
    await storage.put_object(key, pdf, content_type="application/pdf")

    async with tenant_session(workspace_id) as session:
        document = Document(
            id=document_id,
            workspace_id=workspace_id,
            uploaded_by=tenants.user_a,
            original_filename="circular.pdf",
            mime_type="application/pdf",
            size_bytes=len(pdf),
            content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            storage_key_original=key,
            status=DocumentStatus.QUEUED,
        )
        session.add(document)
        await session.flush()
        await ensure_stage_rows(session, document)

    yield document_id, workspace_id

    async with tenant_session(workspace_id) as session:
        doc = await session.scalar(select(Document).where(Document.id == document_id))
        if doc is not None:
            await session.delete(doc)
    await storage.delete_document_objects(workspace_id, document_id)


# --------------------------------------------------------------------------


async def test_pipeline_completes_all_stages(uploaded) -> None:
    document_id, workspace_id = uploaded
    summary = await process_document(document_id, workspace_id)

    assert "failed_stage" not in summary, summary

    async with tenant_session(workspace_id) as session:
        document = await session.scalar(select(Document).where(Document.id == document_id))
        assert document.status is DocumentStatus.COMPLETED
        assert document.page_count == 1
        assert document.storage_key_canonical

        stages = (
            (
                await session.execute(
                    select(DocumentStage).where(DocumentStage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(stages) == len(PipelineStage.ordered())
        assert all(s.status is StageStatus.COMPLETED for s in stages), [
            (s.stage.value, s.status.value, s.error_code) for s in stages
        ]
        assert all(s.idempotency_key for s in stages)


async def test_provenance_chain_survives_into_the_database(uploaded) -> None:
    """document -> page -> char span -> rectangle -> fact -> due date."""
    document_id, workspace_id = uploaded
    await process_document(document_id, workspace_id)

    async with tenant_session(workspace_id) as session:
        pages = (
            (
                await session.execute(
                    select(DocumentPage).where(DocumentPage.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
        assert pages
        assert pages[0].text_map["words"], "page has no word boxes"

        facts = (
            (
                await session.execute(
                    select(DocumentFact).where(DocumentFact.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
        assert facts, "no facts persisted"

        for fact in facts:
            assert fact.source_quote
            assert fact.source_page >= 1
            assert fact.citation_status is not CitationStatus.UNVERIFIED
            if fact.citation_status is CitationStatus.VERIFIED:
                assert fact.source_rects, "verified fact must carry rectangles"
                for rect in fact.source_rects:
                    assert 0.0 <= rect["x0"] < rect["x1"] <= 1.0
                    assert 0.0 <= rect["y0"] < rect["y1"] <= 1.0


async def test_deadline_is_persisted_with_a_normalised_due_date(uploaded) -> None:
    document_id, workspace_id = uploaded
    await process_document(document_id, workspace_id)

    async with tenant_session(workspace_id) as session:
        deadline = await session.scalar(
            select(DocumentFact).where(
                DocumentFact.document_id == document_id,
                DocumentFact.kind == FactKind.DEADLINE,
            )
        )
        assert deadline is not None
        assert deadline.due_at is not None
        assert (deadline.due_at.year, deadline.due_at.month, deadline.due_at.day) == (2026, 9, 15)
        assert deadline.normalization_status == "ok"
        # Raw text preserved so the UI can show what the document said.
        assert deadline.payload["raw_date_text"]


async def test_chunks_and_embeddings_are_written(uploaded) -> None:
    document_id, workspace_id = uploaded
    summary = await process_document(document_id, workspace_id)

    async with tenant_session(workspace_id) as session:
        chunks = (
            (
                await session.execute(
                    select(DocumentChunk).where(DocumentChunk.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
        assert chunks
        for chunk in chunks:
            assert chunk.page_start == chunk.page_end, "a chunk must not span pages"
            assert chunk.content_sha256

        vectors = (
            (
                await session.execute(
                    select(ChunkEmbedding).where(
                        ChunkEmbedding.chunk_id.in_([c.id for c in chunks])
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(vectors) == len(chunks), "every chunk must end up with a vector"

    # Every chunk is accounted for either as freshly embedded or as a cache hit.
    # `embedded` alone can legitimately be 0: the embedding cache is global and
    # keyed by content hash, so a re-run of identical text costs nothing.
    stats = summary["stages"]["embedding"]  # type: ignore[index]
    assert stats["embedded"] + stats["cache_hits"] == len(chunks)  # type: ignore[index]


async def test_page_images_are_rendered_to_storage(uploaded) -> None:
    document_id, workspace_id = uploaded
    await process_document(document_id, workspace_id)

    image = await storage.get_object(storage.page_image_key(workspace_id, document_id, 1))
    assert image[:4] == b"RIFF" and image[8:12] == b"WEBP"


# --------------------------------------------------------------------------
# resumability — the reason stage state lives in Postgres
# --------------------------------------------------------------------------


async def test_rerunning_a_completed_pipeline_skips_every_stage(uploaded) -> None:
    """Idempotency: a second run must not re-extract, re-embed or re-spend."""
    document_id, workspace_id = uploaded
    await process_document(document_id, workspace_id)

    second = await process_document(document_id, workspace_id)
    assert second["stages"] == {}, "a completed pipeline re-ran work it had already done"


async def test_pipeline_resumes_from_the_failed_stage(uploaded) -> None:
    """Simulates a worker killed mid-run.

    Stages before the reset point keep their results; only the reset ones run
    again. On a 100-page scanned document this is the difference between
    re-spending the whole extraction budget and re-spending none of it.
    """
    document_id, workspace_id = uploaded
    await process_document(document_id, workspace_id)

    async with tenant_session(workspace_id) as session:
        reset = await reset_stages_from(session, document_id, PipelineStage.EXTRACTING)
    assert reset == 5  # extracting, aligning, chunking, embedding, indexing

    summary = await process_document(document_id, workspace_id)
    ran = set(summary["stages"])  # type: ignore[arg-type]

    assert "extracting" in ran
    assert "indexing" in ran
    # Parsing and normalising were already done and must not have re-run.
    assert "parsing" not in ran
    assert "normalizing" not in ran

    async with tenant_session(workspace_id) as session:
        document = await session.scalar(select(Document).where(Document.id == document_id))
        assert document.status is DocumentStatus.COMPLETED


async def test_reprocessing_does_not_duplicate_facts(uploaded) -> None:
    document_id, workspace_id = uploaded
    await process_document(document_id, workspace_id)

    async with tenant_session(workspace_id) as session:
        before = len(
            (
                await session.execute(
                    select(DocumentFact).where(DocumentFact.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
        await reset_stages_from(session, document_id, PipelineStage.EXTRACTING)

    await process_document(document_id, workspace_id)

    async with tenant_session(workspace_id) as session:
        after = len(
            (
                await session.execute(
                    select(DocumentFact).where(DocumentFact.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )
    assert after == before, "re-extraction duplicated facts instead of replacing them"


# --------------------------------------------------------------------------
# failure handling
# --------------------------------------------------------------------------


async def test_unsupported_file_fails_with_an_actionable_code(tenants) -> None:
    await storage.ensure_bucket()
    document_id = uuid.uuid4()
    workspace_id = tenants.ws_a

    key = storage.quarantine_key(workspace_id, document_id, "payload.bin")
    await storage.put_object(
        key, b"\x00\x01\x02\x03not a real document", content_type="application/octet-stream"
    )

    async with tenant_session(workspace_id) as session:
        document = Document(
            id=document_id,
            workspace_id=workspace_id,
            original_filename="payload.bin",
            mime_type="application/octet-stream",
            size_bytes=24,
            content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            storage_key_original=key,
            status=DocumentStatus.QUEUED,
        )
        session.add(document)
        await session.flush()
        await ensure_stage_rows(session, document)

    summary = await process_document(document_id, workspace_id)
    assert summary["failed_stage"] == "validating"
    assert summary["error_code"] == "INVALID_FILE_TYPE"

    async with tenant_session(workspace_id) as session:
        document = await session.scalar(select(Document).where(Document.id == document_id))
        assert document.status is DocumentStatus.FAILED
        assert document.error_code == "INVALID_FILE_TYPE"
        # The message must be something a user can act on, not a stack trace.
        assert "PDF" in document.error_detail
        await session.delete(document)
