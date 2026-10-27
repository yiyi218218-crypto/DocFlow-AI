"""Document lifecycle: upload, list, analysis, delete.

Services are the only place that writes to the database. Routes never touch the
ORM directly — that is what keeps tenant filtering, audit logging and quota
enforcement in one place instead of scattered across every handler.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.core import storage
from docflow.core.config import settings
from docflow.core.errors import DocumentTooLarge, DuplicateDocument, NotFound, QuotaExceeded
from docflow.core.logging import get_logger
from docflow.models.document import Document, DocumentFact, DocumentPage
from docflow.models.enums import (
    CitationStatus,
    ConfidenceBucket,
    DocumentStatus,
    FactKind,
)
from docflow.schemas.common import decode_cursor, encode_cursor
from docflow.services import usage_service

logger = get_logger(__name__)


async def create_document(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    content: bytes,
    declared_mime: str | None = None,
) -> tuple[Document, bool]:
    """Persist an upload. Returns (document, is_duplicate).

    Content-hash dedup happens before anything is stored, so re-uploading the
    same circular costs nothing rather than re-running the whole pipeline.
    """
    if len(content) > settings.max_upload_bytes:
        raise DocumentTooLarge(
            f"File is {len(content) / 1_048_576:.1f} MB; the limit is "
            f"{settings.max_upload_bytes / 1_048_576:.0f} MB."
        )

    sha256 = hashlib.sha256(content).hexdigest()

    existing = await session.scalar(
        select(Document).where(
            Document.workspace_id == workspace_id,
            Document.content_sha256 == sha256,
            Document.deleted_at.is_(None),
        )
    )
    if existing is not None:
        logger.info("document.duplicate", document_id=str(existing.id))
        raise DuplicateDocument(
            "You have already uploaded this document.",
            duplicate_of=str(existing.id),
        )

    await _enforce_quota(session, workspace_id)

    document_id = uuid.uuid4()
    key = storage.quarantine_key(workspace_id, document_id, filename)
    await storage.put_object(key, content, content_type=declared_mime or "application/octet-stream")

    document = Document(
        id=document_id,
        workspace_id=workspace_id,
        uploaded_by=user_id,
        original_filename=filename[:500],
        mime_type=declared_mime or "application/octet-stream",
        size_bytes=len(content),
        content_sha256=sha256,
        storage_key_original=key,
        status=DocumentStatus.QUEUED,
    )
    session.add(document)
    await session.flush()

    from docflow.pipeline.orchestrator import ensure_stage_rows

    await ensure_stage_rows(session, document)
    await usage_service.increment_documents(workspace_id, session)

    logger.info(
        "document.created",
        document_id=str(document.id),
        size_bytes=len(content),
        filename=filename,
    )
    return document, False


async def _enforce_quota(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Free-tier caps, enforced in code. Billing is a Phase 4 concern; cost
    protection is not."""
    used = await usage_service.documents_this_month(workspace_id, session)
    if used >= settings.free_tier_documents_per_month:
        raise QuotaExceeded(
            f"You have used all {settings.free_tier_documents_per_month} documents "
            "in your monthly allowance. It resets at the start of next month."
        )


def _base_query(workspace_id: uuid.UUID) -> Select[Any]:
    return select(Document).where(
        Document.workspace_id == workspace_id,
        Document.deleted_at.is_(None),
    )


async def list_documents(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = 25,
    status: DocumentStatus | None = None,
    query: str | None = None,
) -> tuple[list[Document], str | None, bool]:
    """Keyset pagination over (created_at, id)."""
    statement = _base_query(workspace_id)

    if status is not None:
        statement = statement.where(Document.status == status)
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            Document.title.ilike(pattern)
            | Document.original_filename.ilike(pattern)
            | Document.summary.ilike(pattern)
        )

    if cursor and (decoded := decode_cursor(cursor)):
        created_at, item_id = decoded
        statement = statement.where(
            (Document.created_at < created_at)
            | ((Document.created_at == created_at) & (Document.id < uuid.UUID(item_id)))
        )

    statement = statement.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit + 1)
    rows = list((await session.execute(statement)).scalars().all())

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = encode_cursor(rows[-1].created_at, rows[-1].id) if rows and has_more else None
    return rows, next_cursor, has_more


async def get_document(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    document = cast(
        Document | None,
        await session.scalar(_base_query(workspace_id).where(Document.id == document_id)),
    )
    if document is None:
        # 404 rather than 403 — a 403 confirms the id exists somewhere (T6).
        raise NotFound("Document not found.")
    return document


async def fact_counts(
    session: AsyncSession, *, document_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    """Aggregate counts for the library list, in one query rather than N."""
    if not document_ids:
        return {}

    rows = (
        await session.execute(
            select(
                DocumentFact.document_id,
                func.count().label("total"),
                func.count().filter(DocumentFact.kind == FactKind.DEADLINE).label("deadlines"),
                func.min(DocumentFact.due_at)
                .filter(DocumentFact.due_at.is_not(None))
                .label("next_due"),
            )
            .where(DocumentFact.document_id.in_(document_ids))
            .group_by(DocumentFact.document_id)
        )
    ).all()

    return {
        row.document_id: {
            "fact_count": row.total,
            "deadline_count": row.deadlines,
            "next_deadline_at": row.next_due,
        }
        for row in rows
    }


async def get_analysis(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    include_low_confidence: bool = False,
    kinds: list[FactKind] | None = None,
) -> dict[str, Any]:
    """Everything the document detail page needs, in one round trip."""
    document = await get_document(session, workspace_id=workspace_id, document_id=document_id)

    fact_query = select(DocumentFact).where(DocumentFact.document_id == document_id)
    if kinds:
        fact_query = fact_query.where(DocumentFact.kind.in_(kinds))

    all_facts = list((await session.execute(fact_query)).scalars().all())

    # Low-confidence facts are collapsed rather than dropped: hiding them
    # entirely loses recall, showing them inline loses trust.
    visible = (
        all_facts
        if include_low_confidence
        else [f for f in all_facts if f.confidence_bucket is not ConfidenceBucket.LOW]
    )
    hidden = len(all_facts) - len(visible)

    pages = list(
        (
            await session.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_number)
            )
        )
        .scalars()
        .all()
    )

    counts: dict[str, int] = {}
    for fact in visible:
        counts[fact.kind.value] = counts.get(fact.kind.value, 0) + 1

    buckets: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for fact in all_facts:
        buckets[fact.confidence_bucket.value] += 1

    verified = sum(1 for f in all_facts if f.citation_status is CitationStatus.VERIFIED)

    return {
        "document": document,
        "facts": sorted(
            visible,
            key=lambda f: (0 if f.kind is FactKind.DEADLINE else 1, -f.confidence_calibrated),
        ),
        "pages": pages,
        "counts_by_kind": counts,
        "confidence_summary": buckets,
        "citation_verification_rate": (round(verified / len(all_facts), 3) if all_facts else None),
        "low_confidence_hidden": hidden,
    }


async def soft_delete(
    session: AsyncSession, *, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    """Soft delete with a 30-day window. The purge job hard-deletes later."""
    document = await get_document(session, workspace_id=workspace_id, document_id=document_id)
    document.deleted_at = datetime.now(UTC)
    logger.info("document.deleted", document_id=str(document_id))


async def page_image_urls(document: Document, pages: list[DocumentPage]) -> dict[int, str]:
    """Presigned page-image URLs, minted only after the caller was authorised."""
    urls: dict[int, str] = {}
    for page in pages:
        if page.storage_key_image:
            urls[page.page_number] = storage.presign_get(
                page.storage_key_image,
                expires_in=settings.presign_image_ttl_seconds,
                content_type="image/webp",
            )
    _ = document
    return urls
