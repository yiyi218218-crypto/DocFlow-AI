"""ARQ worker.

Same package and same image as the API, different entrypoint. Splitting them
into separate codebases means duplicating models, settings, the storage adapter
and the LLM client — which is exactly where drift bugs breed.

ARQ over Celery because the pipeline's wall-clock is dominated by *awaiting
network I/O* (model calls, embeddings, object storage). Async concurrency lets
one process hold many documents in flight; Celery's process-per-task model would
need one process each. CPU-bound work (PDF parsing, OCR) goes to a thread or
process pool rather than blocking the loop.
"""

from __future__ import annotations

import uuid
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from docflow.core.config import settings
from docflow.core.logging import configure_logging, get_logger, request_id_var
from docflow.pipeline.orchestrator import process_document

logger = get_logger(__name__)


async def process_document_job(
    ctx: dict[str, Any],
    document_id: str,
    workspace_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Advance one document through the pipeline.

    The request id travels in the job payload so a single grep reconstructs the
    whole lifecycle across API and worker logs. Without that link, a user
    reporting "my document failed" gives you an id that dead-ends at the 202.
    """
    if request_id:
        request_id_var.set(request_id)

    logger.info("job.started", job="process_document", document_id=document_id)
    return await process_document(uuid.UUID(document_id), uuid.UUID(workspace_id))


async def schedule_reminders(ctx: dict[str, Any]) -> dict[str, Any]:
    """Deadline reminders at T-14/7/3/1/0.

    Exactly-once is enforced by a unique index on
    (task_id, user_id, offset_days) rather than by careful scheduling, so
    overlapping cron runs are harmless.
    """
    from docflow.services.notification_service import schedule_due_reminders

    return await schedule_due_reminders()


async def purge_deleted(ctx: dict[str, Any]) -> dict[str, Any]:
    """Hard-delete documents soft-deleted more than 30 days ago."""
    from docflow.services.retention_service import purge_expired

    return await purge_expired()


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(
        level=settings.log_level,
        json_output=not settings.is_local,
        service="worker",
    )
    logger.info(
        "worker.started",
        environment=settings.environment,
        demo_mode=settings.effective_demo_mode,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker.stopped")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


class WorkerSettings:
    functions = [process_document_job]
    cron_jobs = [
        cron(schedule_reminders, minute={0, 15, 30, 45}, run_at_startup=False),
        cron(purge_deleted, hour=3, minute=0, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()

    # Async concurrency, not process concurrency: these jobs mostly wait on the
    # network. CPU-bound stages hand off to a thread pool inside the stage.
    max_jobs = 8
    job_timeout = 900  # 15 min — a 150-page scanned document is slow
    keep_result = 3600
    max_tries = 3
    # Backoff between retries. Jitter is applied inside the LLM client where
    # the thundering-herd risk actually lives.
    retry_jobs = True
    health_check_interval = 30
