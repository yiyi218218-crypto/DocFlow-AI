"""Tasks, comments, notifications — the action layer.

Tasks are a real table rather than another fact kind because they have an
independent lifecycle: status transitions, assignment, comments and reminders.
Facts are immutable observations about a document; tasks are mutable objects with
a life of their own. That is the line for promoting a kind out of `document_facts`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from docflow.core.database import Base
from docflow.models.enums import (
    NotificationType,
    TaskOrigin,
    TaskPriority,
    TaskStatus,
)
from docflow.models.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    WorkspaceScopedMixin,
    pg_enum,
    uuid_fk,
    uuid_pk,
)


class Task(Base, TimestampMixin, WorkspaceScopedMixin, SoftDeleteMixin):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID | None] = uuid_fk("documents.id", nullable=True)
    # SET NULL, not CASCADE: a task the user is working on must survive
    # re-extraction of its source document.
    source_fact_id: Mapped[uuid.UUID | None] = uuid_fk(
        "document_facts.id", ondelete="SET NULL", nullable=True
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    status: Mapped[TaskStatus] = mapped_column(
        pg_enum(TaskStatus, "task_status"),
        default=TaskStatus.TODO,
        server_default="todo",
        nullable=False,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        pg_enum(TaskPriority, "task_priority"),
        default=TaskPriority.MEDIUM,
        server_default="medium",
        nullable=False,
    )

    due_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]

    assignee_id: Mapped[uuid.UUID | None] = uuid_fk("users.id", ondelete="SET NULL", nullable=True)
    created_by: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )

    origin: Mapped[TaskOrigin] = mapped_column(
        pg_enum(TaskOrigin, "task_origin"),
        default=TaskOrigin.USER,
        server_default="user",
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    comments: Mapped[list[TaskComment]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # An AI-generated task must be grounded in a fact. No ungrounded tasks.
        CheckConstraint(
            "origin <> 'ai' OR source_fact_id IS NOT NULL",
            name="ai_tasks_need_source_fact",
        ),
        Index(
            "ix_tasks_workspace_status_due",
            "workspace_id",
            "status",
            "due_at",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_tasks_assignee_status",
            "assignee_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    @property
    def is_overdue(self) -> bool:
        if self.due_at is None or self.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
            return False
        return self.due_at < datetime.now(self.due_at.tzinfo)


class TaskComment(Base, WorkspaceScopedMixin):
    __tablename__ = "task_comments"

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = uuid_fk("tasks.id")
    user_id: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    task: Mapped[Task] = relationship(back_populates="comments")


class Notification(Base, WorkspaceScopedMixin):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    type: Mapped[NotificationType] = mapped_column(
        pg_enum(NotificationType, "notification_type"), nullable=False
    )

    task_id: Mapped[uuid.UUID | None] = uuid_fk("tasks.id", nullable=True)
    document_id: Mapped[uuid.UUID | None] = uuid_fk("documents.id", nullable=True)
    # T-14 / T-7 / T-3 / T-1 / T-0 reminder offsets.
    offset_days: Mapped[int | None] = mapped_column(Integer)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="in_app", server_default="in_app")

    read_at: Mapped[datetime | None]
    sent_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    __table_args__ = (
        # Makes reminder delivery exactly-once regardless of cron overlap.
        Index(
            "uq_notifications_task_reminder",
            "task_id",
            "user_id",
            "offset_days",
            unique=True,
            postgresql_where=text("task_id IS NOT NULL AND offset_days IS NOT NULL"),
        ),
        Index(
            "ix_notifications_user_unread",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )
