"""Enqueue side of the job system.

Kept separate from the worker so the API imports only this — the API should never
import stage code, and this file is the seam that makes swapping the queue
library a contained change.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from docflow.core.config import settings
from docflow.core.logging import get_logger, request_id_var

logger = get_logger(__name__)

_pool: ArqRedis | None = None


async def get_queue() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def close_queue() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


# Inline tasks are tracked so they are not garbage-collected mid-flight, which
# asyncio does not prevent on its own.
_inline_tasks: set[asyncio.Task[Any]] = set()


async def enqueue_document(document_id: uuid.UUID, workspace_id: uuid.UUID) -> str | None:
    """Queue a document for processing.

    `_job_id` is the document id, so a double-submit collapses into one job
    rather than running the pipeline twice.
    """
    if settings.worker_mode == "inline":
        return _run_inline(document_id, workspace_id)

    queue = await get_queue()
    job = await queue.enqueue_job(
        "process_document_job",
        str(document_id),
        str(workspace_id),
        request_id_var.get(),
        _job_id=f"doc:{document_id}",
    )
    if job is None:
        logger.info("queue.already_enqueued", document_id=str(document_id))
        return None
    logger.info("queue.enqueued", document_id=str(document_id), job_id=job.job_id)
    return str(job.job_id)


def _run_inline(document_id: uuid.UUID, workspace_id: uuid.UUID) -> str:
    """Process in this process, in the background, without Redis."""
    from docflow.pipeline.orchestrator import process_document

    async def _run() -> None:
        try:
            await process_document(document_id, workspace_id)
        except Exception:
            logger.exception("inline.process_failed", document_id=str(document_id))

    task = asyncio.create_task(_run())
    _inline_tasks.add(task)
    task.add_done_callback(_inline_tasks.discard)

    logger.info("queue.inline", document_id=str(document_id))
    return f"inline:{document_id}"


async def queue_depth() -> dict[str, Any]:
    """Queue depth for the health endpoint and the alerting rule."""
    queue = await get_queue()
    return {"queued": await queue.zcard("arq:queue")}


async def check_redis() -> bool:
    if settings.worker_mode == "inline":
        return True  # not a dependency in this mode
    try:
        queue = await get_queue()
        await queue.ping()
        return True
    except Exception:
        return False
