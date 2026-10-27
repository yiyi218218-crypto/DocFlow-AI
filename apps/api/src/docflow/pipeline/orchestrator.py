"""The stage machine.

The job body is "advance document D to completion". On each invocation it reads
`document_stages`, finds the first stage that is not completed under its current
idempotency key, and runs from there.

That is what buys durable-execution semantics without adopting Temporal:

  * A worker killed mid-extraction resumes at extraction. Pages already parsed
    and OCR'd are not redone — on a 100-page scanned document that is the
    difference between $0.60 and $0.00 of wasted spend.
  * Bumping a prompt version changes one stage's key, so a backfill re-runs only
    that stage.
  * The queue becomes pure delivery. Swapping ARQ for Celery is a change to the
    enqueue call, not to the semantics.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.core.database import tenant_session
from docflow.core.errors import DocFlowError
from docflow.core.logging import get_logger
from docflow.models.document import Document, DocumentStage
from docflow.models.enums import DocumentStatus, PipelineStage, StageStatus
from docflow.pipeline.stages import STAGE_RUNNERS, StageContext, stage_key

logger = get_logger(__name__)

MAX_ATTEMPTS_PER_STAGE = 3

# Stages after which a failure still leaves something worth showing the user.
# Failing to embed must not hide the deadlines we already extracted.
PARTIAL_OK_AFTER = PipelineStage.EXTRACTING


class StageFailed(Exception):
    def __init__(self, stage: PipelineStage, code: str, detail: str, *, retryable: bool) -> None:
        super().__init__(detail)
        self.stage = stage
        self.code = code
        self.detail = detail
        self.retryable = retryable


async def ensure_stage_rows(session: AsyncSession, document: Document) -> None:
    """Create the eleven stage rows for a new document."""
    existing = {
        row.stage
        for row in (
            await session.execute(
                select(DocumentStage).where(DocumentStage.document_id == document.id)
            )
        ).scalars()
    }
    for stage in PipelineStage.ordered():
        if stage not in existing:
            session.add(
                DocumentStage(
                    workspace_id=document.workspace_id,
                    document_id=document.id,
                    stage=stage,
                    status=StageStatus.PENDING,
                )
            )


async def process_document(document_id: uuid.UUID, workspace_id: uuid.UUID) -> dict[str, object]:
    """Advance a document as far as it will go.

    Returns a summary rather than raising, so the queue sees a completed job for
    a document that failed for a non-retryable reason — a corrupt PDF should not
    be retried three times.
    """
    started = time.perf_counter()
    summary: dict[str, object] = {"document_id": str(document_id), "stages": {}}

    async with tenant_session(workspace_id) as session:
        document = await session.scalar(select(Document).where(Document.id == document_id))
        if document is None:
            logger.warning("pipeline.document_missing", document_id=str(document_id))
            return {"error": "not_found"}

        await ensure_stage_rows(session, document)
        stage_rows = {
            row.stage: row
            for row in (
                await session.execute(
                    select(DocumentStage).where(DocumentStage.document_id == document_id)
                )
            ).scalars()
        }

        if document.status in (DocumentStatus.UPLOADED, DocumentStatus.QUEUED):
            document.status = DocumentStatus.PROCESSING
            document.processing_started_at = datetime.now(UTC)

        ctx = StageContext(document=document, session=session)
        reached: PipelineStage | None = None

        for stage in PipelineStage.ordered():
            row = stage_rows[stage]
            key = stage_key(stage, ctx)

            if row.status is StageStatus.COMPLETED and row.idempotency_key == key:
                reached = stage
                continue

            try:
                output = await _run_stage(stage, row, ctx, key)
                summary["stages"][stage.value] = output  # type: ignore[index]
                reached = stage
            except StageFailed as failure:
                _mark_document_failed(document, failure, reached)
                summary["failed_stage"] = stage.value
                summary["error_code"] = failure.code
                logger.error(
                    "pipeline.stage_failed",
                    document_id=str(document_id),
                    stage=stage.value,
                    code=failure.code,
                    attempt=row.attempt,
                    retryable=failure.retryable,
                )
                break
        else:
            document.status = DocumentStatus.COMPLETED
            document.processing_completed_at = datetime.now(UTC)
            document.error_code = None
            document.error_detail = None

    duration_ms = int((time.perf_counter() - started) * 1000)
    summary["duration_ms"] = duration_ms
    logger.info(
        "pipeline.completed",
        document_id=str(document_id),
        status=(summary.get("failed_stage") and "failed") or "completed",
        duration_ms=duration_ms,
    )
    return summary


async def _run_stage(
    stage: PipelineStage, row: DocumentStage, ctx: StageContext, key: str
) -> dict[str, object]:
    runner = STAGE_RUNNERS[stage]
    row.status = StageStatus.RUNNING
    row.attempt += 1
    row.started_at = datetime.now(UTC)
    row.error_code = None
    row.error_detail = None
    await ctx.session.flush()

    started = time.perf_counter()
    try:
        output = await runner(ctx)
    except DocFlowError as exc:
        # A typed error is a decision we already made about this document
        # (encrypted, unsupported, too many pages). Never retried.
        row.status = StageStatus.FAILED
        row.error_code = exc.code
        row.error_detail = exc.detail
        row.finished_at = datetime.now(UTC)
        await ctx.session.flush()
        raise StageFailed(stage, exc.code, exc.detail, retryable=False) from exc
    except Exception as exc:
        row.status = StageStatus.FAILED
        row.error_code = "STAGE_ERROR"
        row.error_detail = f"{type(exc).__name__}: {exc}"[:500]
        row.finished_at = datetime.now(UTC)
        await ctx.session.flush()
        retryable = row.attempt < MAX_ATTEMPTS_PER_STAGE
        logger.exception("pipeline.stage_exception", stage=stage.value)
        raise StageFailed(stage, "STAGE_ERROR", str(exc)[:300], retryable=retryable) from exc

    row.status = StageStatus.COMPLETED
    row.idempotency_key = key
    row.finished_at = datetime.now(UTC)
    row.duration_ms = int((time.perf_counter() - started) * 1000)
    row.output_ref = output
    await ctx.session.flush()

    logger.info(
        "pipeline.stage.completed",
        document_id=str(ctx.document_id),
        stage=stage.value,
        duration_ms=row.duration_ms,
        **{k: v for k, v in output.items() if isinstance(v, int | float | str | bool)},
    )
    return output


def _mark_document_failed(
    document: Document, failure: StageFailed, reached: PipelineStage | None
) -> None:
    """Partial beats failed when there is already something useful to show."""
    document.error_code = failure.code
    document.error_detail = failure.detail
    document.processing_completed_at = datetime.now(UTC)

    if reached is not None and reached.position >= PARTIAL_OK_AFTER.position:
        document.status = DocumentStatus.PARTIAL
    else:
        document.status = DocumentStatus.FAILED


async def reset_stages_from(
    session: AsyncSession, document_id: uuid.UUID, stage: PipelineStage
) -> int:
    """Mark a stage and everything after it pending, for reprocessing."""
    rows = (
        (
            await session.execute(
                select(DocumentStage).where(DocumentStage.document_id == document_id)
            )
        )
        .scalars()
        .all()
    )

    reset = 0
    for row in rows:
        if row.stage.position >= stage.position:
            row.status = StageStatus.PENDING
            row.idempotency_key = None
            row.attempt = 0
            row.error_code = None
            row.error_detail = None
            reset += 1
    return reset
