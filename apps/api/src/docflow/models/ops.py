"""Operational tables: usage, audit, idempotency, caches, chat, evaluation.

`usage_records` is the load-bearing one. One append-only row per LLM / embedding /
OCR call powers the cost dashboard, per-workspace budgets, the eval cost report and
future metered billing. Build it in week 1 — retrofitting cost attribution is
miserable. docs/20-ai.md §20.5
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from docflow.core.config import settings
from docflow.core.database import Base
from docflow.models.mixins import (
    TimestampMixin,
    WorkspaceScopedMixin,
    uuid_fk,
    uuid_pk,
)


class UsageRecord(Base, WorkspaceScopedMixin):
    """Append-only. Never updated, never deleted."""

    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    document_id: Mapped[uuid.UUID | None] = uuid_fk("documents.id", nullable=True, index=False)

    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str | None] = mapped_column(String(64))

    # server_default as well as default: these columns must be safe to omit from a
    # raw INSERT (backfills, psql, the eval harness), not only from an ORM insert.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Sub-cent precision matters when a call costs $0.000015.
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    error_code: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    __table_args__ = (Index("ix_usage_records_workspace_created", "workspace_id", "created_at"),)


class UsageDaily(Base):
    """Hourly rollup of usage_records. Quota checks must not scan the raw table."""

    __tablename__ = "usage_daily"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), primary_key=True)

    documents_processed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_processed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    """Append-only. Deliberately no FKs — an audit record must survive the
    deletion of whatever it describes."""

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    actor_ip: Mapped[str | None] = mapped_column(INET)

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    audit_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_workspace_created", "workspace_id", "created_at"),
    )


class IdempotencyKey(Base):
    """Safe POST retries: same key + same body replays the stored response,
    same key + different body is a 409."""

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(nullable=False, index=True)


class EmbeddingCache(Base):
    """Global, cross-tenant, keyed by content hash.

    Safe to share across workspaces: the key is a hash of the text, so a hit
    requires already possessing identical text. Institutional circulars recycle
    boilerplate heavily, so hit rates are meaningful.
    """

    __tablename__ = "embedding_cache"

    content_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    embedding: Mapped[Any] = mapped_column(Vector(settings.embedding_dimensions), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Conversation(Base, TimestampMixin, WorkspaceScopedMixin):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    document_id: Mapped[uuid.UUID | None] = uuid_fk("documents.id", nullable=True)
    title: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(16), default="document", server_default="document")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base, WorkspaceScopedMixin):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = uuid_fk("conversations.id")
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(120))
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retrieval_strategy: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class EvalRun(Base):
    """Evaluation is a first-class subsystem, not a script."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    suite: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    git_sha: Mapped[str | None] = mapped_column(String(40))
    model_id: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    dataset_version: Mapped[str | None] = mapped_column(String(64))

    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    results: Mapped[list[EvalResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = uuid_fk("eval_runs.id")
    item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    predicted: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    expected: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped[EvalRun] = relationship(back_populates="results")
