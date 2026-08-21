"""Dashboard: everything the landing screen needs, in one request.

Deliberately one endpoint rather than five. The dashboard is the first screen
after sign-in, so its latency is the app's perceived latency, and five parallel
round trips to a free-tier instance that may be cold is the wrong trade. The
queries are all indexed and tenant-scoped.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import case, func, select

from docflow.api.v1.deps import CurrentPrincipal, TenantSession
from docflow.models.document import Document, DocumentFact
from docflow.models.enums import (
    CitationStatus,
    ConfidenceBucket,
    DocumentStatus,
    FactKind,
    TaskStatus,
)
from docflow.models.work import Task

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

UPCOMING_WINDOW_DAYS = 60


@router.get("")
async def get_dashboard(
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    workspace_id = principal.workspace_id
    now = datetime.now(UTC)
    horizon = now + timedelta(days=UPCOMING_WINDOW_DAYS)

    # --- documents -------------------------------------------------------
    doc_stats = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count()
                .filter(Document.status.in_([DocumentStatus.QUEUED, DocumentStatus.PROCESSING]))
                .label("processing"),
                func.coalesce(func.sum(Document.page_count), 0).label("pages"),
                func.coalesce(func.sum(Document.total_cost_usd), 0).label("cost"),
            ).where(Document.workspace_id == workspace_id, Document.deleted_at.is_(None))
        )
    ).one()

    # --- facts + the flagship metric -------------------------------------
    fact_stats = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(DocumentFact.kind == FactKind.DEADLINE).label("deadlines"),
                func.count()
                .filter(DocumentFact.kind == FactKind.REQUIREMENT)
                .label("requirements"),
                func.count()
                .filter(DocumentFact.citation_status == CitationStatus.VERIFIED)
                .label("verified"),
                func.count()
                .filter(DocumentFact.confidence_bucket == ConfidenceBucket.HIGH)
                .label("high_confidence"),
            ).where(DocumentFact.workspace_id == workspace_id)
        )
    ).one()

    # --- upcoming deadlines, joined to their document and any task -------
    upcoming_rows = (
        await session.execute(
            select(DocumentFact, Document.title, Document.original_filename, Task.id, Task.status)
            .join(Document, Document.id == DocumentFact.document_id)
            .outerjoin(Task, Task.source_fact_id == DocumentFact.id)
            .where(
                DocumentFact.workspace_id == workspace_id,
                DocumentFact.kind == FactKind.DEADLINE,
                DocumentFact.due_at.is_not(None),
                DocumentFact.due_at <= horizon,
                Document.deleted_at.is_(None),
            )
            .order_by(DocumentFact.due_at.asc())
            .limit(8)
        )
    ).all()

    upcoming = [
        {
            "fact_id": str(fact.id),
            "document_id": str(fact.document_id),
            "document_title": title or filename,
            "label": str(fact.payload.get("label") or "Deadline"),
            "due_at": fact.due_at.isoformat(),
            "raw_date_text": fact.payload.get("raw_date_text"),
            "days_remaining": (fact.due_at - now).days,
            "is_primary": fact.is_primary,
            "confidence_bucket": fact.confidence_bucket.value,
            "normalization_status": fact.normalization_status,
            "source_page": fact.source_page,
            "task_id": str(task_id) if task_id else None,
            "task_status": task_status.value if task_status else None,
        }
        for fact, title, filename, task_id, task_status in upcoming_rows
    ]

    # --- tasks -----------------------------------------------------------
    task_stats = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Task.status == TaskStatus.DONE).label("done"),
                func.count()
                .filter(
                    Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]),
                    Task.due_at.is_not(None),
                    Task.due_at < now,
                )
                .label("overdue"),
            ).where(Task.workspace_id == workspace_id, Task.deleted_at.is_(None))
        )
    ).one()

    open_tasks_rows = (
        await session.execute(
            select(Task, Document.title, Document.original_filename)
            .outerjoin(Document, Document.id == Task.document_id)
            .where(
                Task.workspace_id == workspace_id,
                Task.deleted_at.is_(None),
                Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]),
            )
            .order_by(
                # Dated work first, soonest first; undated work after it.
                case((Task.due_at.is_(None), 1), else_=0),
                Task.due_at.asc(),
                Task.sort_order.asc(),
            )
            .limit(6)
        )
    ).all()

    open_tasks = [
        {
            "id": str(task.id),
            "title": task.title,
            "status": task.status.value,
            "priority": task.priority.value,
            "due_at": task.due_at.isoformat() if task.due_at else None,
            "origin": task.origin.value,
            "document_id": str(task.document_id) if task.document_id else None,
            "document_title": title or filename,
        }
        for task, title, filename in open_tasks_rows
    ]

    recent_rows = (
        await session.execute(
            select(Document)
            .where(Document.workspace_id == workspace_id, Document.deleted_at.is_(None))
            .order_by(Document.created_at.desc())
            .limit(5)
        )
    ).scalars().all()

    verified_rate = (
        round(fact_stats.verified / fact_stats.total, 3) if fact_stats.total else None
    )

    return {
        "documents": {
            "total": doc_stats.total,
            "processing": doc_stats.processing,
            "pages": int(doc_stats.pages or 0),
            "cost_usd": round(float(doc_stats.cost or 0), 6),
        },
        "facts": {
            "total": fact_stats.total,
            "deadlines": fact_stats.deadlines,
            "requirements": fact_stats.requirements,
            "verified": fact_stats.verified,
            "high_confidence": fact_stats.high_confidence,
            "citation_verification_rate": verified_rate,
        },
        "tasks": {
            "total": task_stats.total,
            "done": task_stats.done,
            "open": task_stats.total - task_stats.done,
            "overdue": task_stats.overdue,
        },
        "upcoming_deadlines": upcoming,
        "open_tasks": open_tasks,
        "recent_documents": [
            {
                "id": str(d.id),
                "title": d.title or d.original_filename,
                "original_filename": d.original_filename,
                "status": d.status.value,
                "language": d.language.value,
                "document_type": d.document_type.value if d.document_type else None,
                "page_count": d.page_count,
                "created_at": d.created_at.isoformat(),
            }
            for d in recent_rows
        ],
    }
