"""Embedding with a global content-hash cache.

The cache is keyed by (sha256(text), model_id) and is deliberately cross-tenant.
That is safe because a hit requires already possessing byte-identical text — the
key *is* the content. Institutional circulars recycle boilerplate heavily, so the
hit rate is real money.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.ai.llm.client import embed_texts
from docflow.core.config import settings
from docflow.core.logging import get_logger
from docflow.models.document import ChunkEmbedding, DocumentChunk
from docflow.models.ops import EmbeddingCache

logger = get_logger(__name__)

BATCH_SIZE = 96


async def embed_chunks(
    chunks: list[DocumentChunk],
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Embed chunks, persist vectors, and report cache effectiveness."""
    if not chunks:
        return {"embedded": 0, "cache_hits": 0}

    model_id = settings.embedding_model

    hashes = [c.content_sha256 for c in chunks]
    cached_rows = (
        (
            await session.execute(
                select(EmbeddingCache).where(
                    EmbeddingCache.content_sha256.in_(hashes),
                    EmbeddingCache.model_id == model_id,
                )
            )
        )
        .scalars()
        .all()
    )
    cache = {row.content_sha256: row.embedding for row in cached_rows}

    missing = [c for c in chunks if c.content_sha256 not in cache]
    fresh: dict[str, list[float]] = {}

    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start : start + BATCH_SIZE]
        response = await embed_texts(
            [c.content for c in batch],
            operation="embedding",
            workspace_id=workspace_id,
        )
        for chunk, embedded_vector in zip(batch, response.vectors, strict=True):
            fresh[chunk.content_sha256] = embedded_vector

    for sha, fresh_vector in fresh.items():
        # ON CONFLICT DO NOTHING: two documents embedding the same boilerplate
        # concurrently is expected, not an error.
        await session.execute(
            pg_insert(EmbeddingCache)
            .values(content_sha256=sha, model_id=model_id, embedding=fresh_vector)
            .on_conflict_do_nothing(index_elements=["content_sha256", "model_id"])
        )

    for chunk in chunks:
        vector = cache.get(chunk.content_sha256) or fresh.get(chunk.content_sha256)
        if vector is None:
            continue
        session.add(
            ChunkEmbedding(
                workspace_id=chunk.workspace_id,
                chunk_id=chunk.id,
                model_id=model_id,
                embedding=vector,
            )
        )

    hits = len(chunks) - len(missing)
    logger.info(
        "embedding.completed",
        chunks=len(chunks),
        cache_hits=hits,
        embedded=len(fresh),
        model=model_id,
    )
    return {
        "embedded": len(fresh),
        "cache_hits": hits,
        "cache_hit_ratio": round(hits / len(chunks), 3) if chunks else 0.0,
        "model": model_id,
    }
