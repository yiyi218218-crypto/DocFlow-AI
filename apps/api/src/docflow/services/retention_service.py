"""Retention.

Soft delete sets `deleted_at` and keeps the row and its blobs for 30 days, which
covers accidental deletion. After that a purge hard-deletes both. A deletion
request from the user bypasses the window entirely.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from docflow.core import storage
from docflow.core.database import system_session
from docflow.core.logging import get_logger
from docflow.models.document import Document

logger = get_logger(__name__)

RETENTION_DAYS = 30


async def purge_expired(*, older_than_days: int = RETENTION_DAYS) -> dict[str, Any]:
    """Hard-delete documents soft-deleted beyond the retention window.

    Blobs go first: a row without its objects is a recoverable inconsistency,
    while orphaned objects nobody can see are an unbounded storage bill and a
    privacy problem.
    """
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    purged = 0
    objects = 0

    async with system_session() as session:
        documents = (
            (
                await session.execute(
                    select(Document).where(
                        Document.deleted_at.is_not(None),
                        Document.deleted_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )

        for document in documents:
            objects += await _purge_document_objects(document.workspace_id, document.id)
            await session.delete(document)  # cascades to pages, chunks, facts, stages
            purged += 1

    if purged:
        logger.info("retention.purged", documents=purged, objects=objects)
    return {"documents_purged": purged, "objects_deleted": objects}


async def purge_document_now(workspace_id: uuid.UUID, document_id: uuid.UUID) -> dict[str, Any]:
    """Immediate hard delete, for an explicit user or GDPR request."""
    objects = await _purge_document_objects(workspace_id, document_id)
    async with system_session() as session:
        document = await session.scalar(select(Document).where(Document.id == document_id))
        if document is not None:
            await session.delete(document)
    logger.info("retention.purged_now", document_id=str(document_id), objects=objects)
    return {"objects_deleted": objects}


async def _purge_document_objects(workspace_id: uuid.UUID, document_id: uuid.UUID) -> int:
    try:
        return await storage.delete_document_objects(workspace_id, document_id)
    except Exception:
        logger.exception("retention.object_purge_failed", document_id=str(document_id))
        return 0
