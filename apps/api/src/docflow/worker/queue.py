"""Enqueue side of the job system.

Kept separate from the worker so the API imports only this — the API should never
import stage code, and this file is the seam that makes swapping the queue
library a contained change.
"""

from __future__ import annotations

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


async def enqueue_document(document_id: uuid.UUID, workspace_id: uuid.UUID) -> str | None:
    """Queue a document for processing.

    `_job_id` is the document id, so a double-submit collapses into one job
    rather than running the pipeline twice.
    """
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


async def queue_depth() -> dict[str, Any]:
    """Queue depth for the health endpoint and the alerting rule."""
    queue = await get_queue()
    return {"queued": await queue.zcard("arq:queue")}


async def check_redis() -> bool:
    try:
        queue = await get_queue()
        await queue.ping()
        return True
    except Exception:
        return False
