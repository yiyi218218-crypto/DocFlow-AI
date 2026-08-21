"""Deadline reminders.

Exactly-once delivery comes from a unique index on
(task_id, user_id, offset_days), not from careful scheduling. That means the cron
can run every 15 minutes, overlap with itself, or be replayed after an incident,
and a user still receives each reminder once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from docflow.core.database import system_session
from docflow.core.logging import get_logger
from docflow.models.enums import NotificationType, TaskStatus
from docflow.models.work import Notification, Task

logger = get_logger(__name__)

# Days before a deadline at which to remind. T-0 is the day itself.
REMINDER_OFFSETS = (14, 7, 3, 1, 0)

# How wide a window each cron run considers. Wider than the 15-minute cadence so
# a missed run self-heals on the next one.
WINDOW_HOURS = 26


async def schedule_due_reminders() -> dict[str, Any]:
    """Create notification rows for tasks entering a reminder window."""
    now = datetime.now(UTC)
    created = 0
    considered = 0

    async with system_session() as session:
        for offset in REMINDER_OFFSETS:
            target = now + timedelta(days=offset)
            window_start = target - timedelta(hours=WINDOW_HOURS / 2)
            window_end = target + timedelta(hours=WINDOW_HOURS / 2)

            tasks = (
                (
                    await session.execute(
                        select(Task).where(
                            Task.deleted_at.is_(None),
                            Task.due_at.is_not(None),
                            Task.due_at >= window_start,
                            Task.due_at <= window_end,
                            Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]),
                        )
                    )
                )
                .scalars()
                .all()
            )

            for task in tasks:
                considered += 1
                recipient = task.assignee_id or task.created_by
                if recipient is None:
                    continue

                statement = (
                    pg_insert(Notification)
                    .values(
                        workspace_id=task.workspace_id,
                        user_id=recipient,
                        type=NotificationType.DEADLINE_REMINDER,
                        task_id=task.id,
                        document_id=task.document_id,
                        offset_days=offset,
                        title=_title(task.title, offset),
                        body=task.description,
                        channel="in_app",
                    )
                    .on_conflict_do_nothing()
                )
                result = await session.execute(statement)
                created += result.rowcount or 0

    if created:
        logger.info("reminders.scheduled", created=created, considered=considered)
    return {"created": created, "considered": considered}


def _title(task_title: str, offset_days: int) -> str:
    if offset_days == 0:
        return f"Due today: {task_title}"
    if offset_days == 1:
        return f"Due tomorrow: {task_title}"
    return f"Due in {offset_days} days: {task_title}"


async def unread_count(workspace_id: Any, user_id: Any, session: Any) -> int:
    from sqlalchemy import func

    total = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.workspace_id == workspace_id,
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
        )
    )
    return int(total or 0)
