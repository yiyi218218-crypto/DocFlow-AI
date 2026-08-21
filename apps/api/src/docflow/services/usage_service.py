"""The usage ledger.

One append-only row per LLM / embedding / OCR call. This single table answers
"what did last month cost", "which prompt version is expensive", "has this
workspace blown its budget" and, later, "what do we bill" — which is why it is
built in week 1 rather than retrofitted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.ai.llm.base import TokenUsage
from docflow.core.config import settings
from docflow.core.database import system_session
from docflow.models.ops import UsageDaily, UsageRecord


async def record_usage(
    *,
    workspace_id: uuid.UUID,
    operation: str,
    provider: str,
    model_id: str,
    usage: TokenUsage | None = None,
    cost_usd: float = 0.0,
    user_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    prompt_version: str | None = None,
    latency_ms: int | None = None,
    success: bool = True,
    error_code: str | None = None,
    request_id: str | None = None,
    session: AsyncSession | None = None,
) -> None:
    """Append one ledger row and fold it into the daily rollup.

    Uses its own transaction when no session is supplied: accounting must survive
    a caller that later rolls back, otherwise a failed request becomes free
    inference.
    """
    usage = usage or TokenUsage()

    async def _write(db: AsyncSession) -> None:
        db.add(
            UsageRecord(
                workspace_id=workspace_id,
                user_id=user_id,
                document_id=document_id,
                operation=operation,
                provider=provider,
                model_id=model_id,
                prompt_version=prompt_version,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                success=success,
                error_code=error_code,
                request_id=request_id,
            )
        )
        # Upsert the rollup in the same transaction so the quota check never
        # sees a ledger row that has not been counted yet.
        stmt = pg_insert(UsageDaily).values(
            workspace_id=workspace_id,
            day=datetime.now(UTC).date(),
            operation=operation,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost_usd,
        )
        await db.execute(
            stmt.on_conflict_do_update(
                index_elements=[UsageDaily.workspace_id, UsageDaily.day, UsageDaily.operation],
                set_={
                    "input_tokens": UsageDaily.input_tokens + stmt.excluded.input_tokens,
                    "output_tokens": UsageDaily.output_tokens + stmt.excluded.output_tokens,
                    "cost_usd": UsageDaily.cost_usd + stmt.excluded.cost_usd,
                    "updated_at": func.now(),
                },
            )
        )

    if session is not None:
        await _write(session)
    else:
        async with system_session() as db:
            await _write(db)


async def spend_today(workspace_id: uuid.UUID, session: AsyncSession) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(UsageDaily.cost_usd), 0)).where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.day == datetime.now(UTC).date(),
        )
    )
    return float(total or 0.0)


async def budget_remaining(workspace_id: uuid.UUID, session: AsyncSession) -> float:
    """Headroom before the daily cap. Checked before a call, not after."""
    return settings.daily_budget_per_workspace_usd - await spend_today(workspace_id, session)


async def documents_this_month(workspace_id: uuid.UUID, session: AsyncSession) -> int:
    first_of_month = date.today().replace(day=1)
    total = await session.scalar(
        select(func.coalesce(func.sum(UsageDaily.documents_processed), 0)).where(
            UsageDaily.workspace_id == workspace_id,
            UsageDaily.day >= first_of_month,
        )
    )
    return int(total or 0)


async def increment_documents(
    workspace_id: uuid.UUID, session: AsyncSession, *, pages: int = 0
) -> None:
    stmt = pg_insert(UsageDaily).values(
        workspace_id=workspace_id,
        day=datetime.now(UTC).date(),
        operation="document_ingest",
        documents_processed=1,
        pages_processed=pages,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[UsageDaily.workspace_id, UsageDaily.day, UsageDaily.operation],
            set_={
                "documents_processed": UsageDaily.documents_processed + 1,
                "pages_processed": UsageDaily.pages_processed + stmt.excluded.pages_processed,
                "updated_at": func.now(),
            },
        )
    )


async def usage_summary(
    workspace_id: uuid.UUID, session: AsyncSession, *, days: int = 30
) -> dict[str, object]:
    """Powers the usage page and the quota headroom shown to users."""
    rows = (
        (
            await session.execute(
                text(
                    """
                SELECT operation,
                       SUM(input_tokens)  AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       SUM(cost_usd)      AS cost_usd,
                       SUM(documents_processed) AS documents,
                       SUM(pages_processed)     AS pages
                FROM usage_daily
                WHERE workspace_id = :ws AND day >= CURRENT_DATE - :days::int
                GROUP BY operation
                ORDER BY cost_usd DESC
                """
                ),
                {"ws": workspace_id, "days": days},
            )
        )
        .mappings()
        .all()
    )

    total_cost = sum(float(r["cost_usd"] or 0) for r in rows)
    return {
        "period_days": days,
        "total_cost_usd": round(total_cost, 6),
        "documents": sum(int(r["documents"] or 0) for r in rows),
        "pages": sum(int(r["pages"] or 0) for r in rows),
        "by_operation": [dict(r) for r in rows],
        "daily_budget_usd": settings.daily_budget_per_workspace_usd,
        "spend_today_usd": round(await spend_today(workspace_id, session), 6),
    }
