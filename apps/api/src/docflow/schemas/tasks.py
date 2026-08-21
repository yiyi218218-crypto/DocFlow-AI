"""Task and deadline contracts."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from docflow.models.enums import TaskOrigin, TaskPriority, TaskStatus
from docflow.schemas.common import APIModel


class TaskOut(APIModel):
    id: uuid.UUID
    title: str
    description: str | None = None
    status: TaskStatus
    priority: TaskPriority
    due_at: datetime | None = None
    completed_at: datetime | None = None
    origin: TaskOrigin
    document_id: uuid.UUID | None = None
    source_fact_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    sort_order: int = 0
    created_at: datetime

    # Denormalised so a task list can deep-link to its source without N+1.
    document_title: str | None = None
    source_page: int | None = None


class TaskCreateIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(None, max_length=5000)
    due_at: datetime | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    document_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None


class TaskUpdateIn(BaseModel):
    """All optional: PATCH semantics, so absent means unchanged."""

    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = Field(None, max_length=5000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    assignee_id: uuid.UUID | None = None
    sort_order: int | None = None


class TaskGenerateIn(BaseModel):
    fact_ids: list[uuid.UUID] | None = None
    include_low_confidence: bool = False


class TaskGenerateOut(BaseModel):
    created: list[TaskOut]
    skipped_existing: int = 0
    skipped_low_confidence: int = 0


class DeadlineOut(BaseModel):
    """A deadline fact projected for the calendar and dashboard.

    Carries enough provenance to jump straight to the source page, because the
    chain has to survive all the way into the action layer.
    """

    fact_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    label: str
    due_at: datetime
    raw_date_text: str | None = None
    normalization_status: str | None = None
    confidence_bucket: str
    is_primary: bool
    source_page: int
    days_remaining: int
    task_id: uuid.UUID | None = None
    task_status: TaskStatus | None = None
