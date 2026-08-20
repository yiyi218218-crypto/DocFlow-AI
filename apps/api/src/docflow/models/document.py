"""Documents, pipeline stages, pages, chunks, embeddings and facts.

Two schema decisions carry most of the weight here:

1. `document_facts` is ONE table with a `kind` discriminator and a JSONB payload,
   not five typed tables. Deadlines / requirements / entities / action items share
   ~80% of their columns (provenance, confidence, review state), so five tables
   would mean five citation implementations and five places to forget
   `workspace_id`. It is also why a new document vertical needs no migration.

2. Embeddings live in their own table keyed by (chunk_id, model_id) rather than as
   a column on chunks, so two embedding models can coexist during a backfill.

docs/30-data-api.md §12.3
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from docflow.core.config import settings
from docflow.core.database import Base
from docflow.models.enums import (
    CitationStatus,
    ConfidenceBucket,
    DocumentStatus,
    DocumentType,
    ExtractionMethod,
    FactKind,
    Language,
    PipelineStage,
    ReviewStatus,
    StageStatus,
    TextExtractionMethod,
)
from docflow.models.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    WorkspaceScopedMixin,
    pg_enum,
    uuid_fk,
    uuid_pk,
)

if TYPE_CHECKING:
    from docflow.models.identity import Workspace


class Document(Base, TimestampMixin, WorkspaceScopedMixin, SoftDeleteMixin):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_by: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )

    # --- source file ------------------------------------------------------
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    storage_key_original: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key_canonical: Mapped[str | None] = mapped_column(Text)

    # --- derived metadata -------------------------------------------------
    page_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[Language] = mapped_column(
        pg_enum(Language, "language"), default=Language.UNKNOWN, server_default="unknown"
    )
    document_type: Mapped[DocumentType | None] = mapped_column(pg_enum(DocumentType, "document_type"))

    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    organization: Mapped[str | None] = mapped_column(Text)
    reference_number: Mapped[str | None] = mapped_column(String(200))
    published_date: Mapped[datetime | None]

    # --- processing -------------------------------------------------------
    status: Mapped[DocumentStatus] = mapped_column(
        pg_enum(DocumentStatus, "document_status"),
        default=DocumentStatus.UPLOADED,
        server_default="uploaded",
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None]
    processing_completed_at: Mapped[datetime | None]

    # Injection detection results, surfaced as a banner in the UI (T4).
    security_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default="{}", nullable=False
    )

    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    workspace: Mapped[Workspace] = relationship(back_populates="documents")
    stages: Mapped[list[DocumentStage]] = relationship(
        back_populates="document", cascade="all, delete-orphan", lazy="selectin"
    )
    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    facts: Mapped[list[DocumentFact]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Content-hash dedup. A re-upload costs $0 instead of a full pipeline run.
        Index(
            "uq_documents_workspace_sha",
            "workspace_id",
            "content_sha256",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_documents_workspace_created",
            "workspace_id",
            text("created_at DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Tiny partial index — powers the processing poller without scanning
        # the whole table.
        Index(
            "ix_documents_active_status",
            "workspace_id",
            "status",
            postgresql_where=text("status NOT IN ('completed','failed','quarantined')"),
        ),
    )


class DocumentStage(Base, WorkspaceScopedMixin):
    """The pipeline state machine.

    Pipeline state lives in Postgres, not in the queue. That gives resumability
    (a crashed worker restarts at the failed stage) and per-stage idempotency
    without adopting Temporal. docs/10-architecture.md §11.3
    """

    __tablename__ = "document_stages"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = uuid_fk("documents.id")
    stage: Mapped[PipelineStage] = mapped_column(pg_enum(PipelineStage, "pipeline_stage"), nullable=False)
    status: Mapped[StageStatus] = mapped_column(
        pg_enum(StageStatus, "stage_status"),
        default=StageStatus.PENDING,
        server_default="pending",
        nullable=False,
    )

    attempt: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    # Changing a prompt version changes this key, which re-runs only this stage.
    idempotency_key: Mapped[str | None] = mapped_column(String(200))

    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    output_ref: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    document: Mapped[Document] = relationship(back_populates="stages")

    __table_args__ = (
        UniqueConstraint("document_id", "stage", name="uq_document_stages_document_stage"),
    )


class DocumentPage(Base, WorkspaceScopedMixin):
    """One row per page, holding the text map that makes citations possible.

    `text_map` is {"words": [[char_start, char_end, x0, y0, x1, y1], ...]} with
    coordinates normalised to [0,1] against the page DISPLAY box — rotation is
    applied server-side so the frontend never redoes that maths.
    """

    __tablename__ = "document_pages"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = uuid_fk("documents.id")
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    text: Mapped[str] = mapped_column(Text, default="", server_default="")
    text_map: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)

    width: Mapped[float] = mapped_column(Float, default=0.0)
    height: Mapped[float] = mapped_column(Float, default=0.0)
    rotation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    storage_key_image: Mapped[str | None] = mapped_column(Text)

    extraction_method: Mapped[TextExtractionMethod] = mapped_column(
        pg_enum(TextExtractionMethod, "text_extraction_method"),
        default=TextExtractionMethod.NONE,
        server_default="none",
    )
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    # 'span' when we have word boxes, 'page' when the OCR tier lost coordinates.
    citation_granularity: Mapped[str] = mapped_column(
        String(8), default="span", server_default="span"
    )

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="pages")

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_document_page"),
    )


class DocumentChunk(Base, WorkspaceScopedMixin):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = uuid_fk("documents.id")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)

    # Character offsets let chat citations resolve to rectangles through the very
    # same alignment path extraction uses. One implementation, two consumers.
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    section_path: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="chunks")
    embeddings: Mapped[list[ChunkEmbedding]] = relationship(
        back_populates="chunk", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunks_document_index"),
    )


class ChunkEmbedding(Base, WorkspaceScopedMixin):
    """Separate from chunks so two embedding models can coexist during a backfill.

    `workspace_id` is carried here rather than joined through chunks so a filtered
    vector search is a single-table predicate — which is both an RLS requirement
    and what keeps pgvector's iterative index scans efficient.
    """

    __tablename__ = "document_chunk_embeddings"

    id: Mapped[uuid.UUID] = uuid_pk()
    chunk_id: Mapped[uuid.UUID] = uuid_fk("document_chunks.id")
    model_id: Mapped[str] = mapped_column(String(120), nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(settings.embedding_dimensions), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    chunk: Mapped[DocumentChunk] = relationship(back_populates="embeddings")

    __table_args__ = (
        UniqueConstraint("chunk_id", "model_id", name="uq_chunk_embeddings_chunk_model"),
        Index(
            "ix_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )


class DocumentFact(Base, TimestampMixin, WorkspaceScopedMixin):
    """Every typed observation extracted from a document.

    Provenance columns are identical across kinds — which is exactly why they
    share a table.
    """

    __tablename__ = "document_facts"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = uuid_fk("documents.id")
    kind: Mapped[FactKind] = mapped_column(pg_enum(FactKind, "fact_kind"), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    # --- provenance -------------------------------------------------------
    source_page: Mapped[int] = mapped_column(Integer, nullable=False)
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    source_char_start: Mapped[int | None] = mapped_column(Integer)
    source_char_end: Mapped[int | None] = mapped_column(Integer)
    # [{page, x0, y0, x1, y1}] normalised 0..1
    source_rects: Mapped[list[dict[str, Any]] | None] = mapped_column(nullable=True)
    citation_status: Mapped[CitationStatus] = mapped_column(
        pg_enum(CitationStatus, "citation_status"),
        default=CitationStatus.UNVERIFIED,
        server_default="unverified",
        nullable=False,
    )
    citation_match_score: Mapped[float | None] = mapped_column(Float)

    # --- scoring ----------------------------------------------------------
    confidence_raw: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_calibrated: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence_bucket: Mapped[ConfidenceBucket] = mapped_column(
        pg_enum(ConfidenceBucket, "confidence_bucket"),
        default=ConfidenceBucket.LOW,
        server_default="low",
        nullable=False,
    )

    # --- lineage ----------------------------------------------------------
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        pg_enum(ExtractionMethod, "extraction_method"),
        default=ExtractionMethod.LLM_EXTRACTION,
        server_default="llm_extraction",
        nullable=False,
    )
    model_id: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    extracted_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # --- review -----------------------------------------------------------
    review_status: Mapped[ReviewStatus] = mapped_column(
        pg_enum(ReviewStatus, "review_status"),
        default=ReviewStatus.PENDING,
        server_default="pending",
        nullable=False,
    )
    reviewed_by: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    reviewed_at: Mapped[datetime | None]

    # --- promoted from payload for indexing -------------------------------
    # Rule: promote a field the moment it appears in a WHERE or ORDER BY.
    due_at: Mapped[datetime | None]
    normalization_status: Mapped[str | None] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    document: Mapped[Document] = relationship(back_populates="facts")

    __table_args__ = (
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_is_object"),
        CheckConstraint(
            "kind <> 'deadline' OR payload ? 'raw_date_text'",
            name="deadline_needs_raw_date_text",
        ),
        Index("ix_document_facts_document_kind", "document_id", "kind"),
        # Partial index: drives the calendar and the reminder cron.
        Index(
            "ix_document_facts_due",
            "workspace_id",
            "due_at",
            postgresql_where=text("kind = 'deadline' AND due_at IS NOT NULL"),
        ),
        Index(
            "ix_document_facts_pending_review",
            "workspace_id",
            "review_status",
            postgresql_where=text("review_status = 'pending'"),
        ),
    )


class FactReview(Base, WorkspaceScopedMixin):
    """User confirm / edit / reject feedback.

    This is the evaluation flywheel: every correction is a free labelled example,
    and the acceptance rate is a better quality signal than any offline benchmark.
    """

    __tablename__ = "fact_reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    fact_id: Mapped[uuid.UUID] = uuid_fk("document_facts.id")
    user_id: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # confirm | edit | reject
    original_payload: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    corrected_payload: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    __table_args__ = (
        CheckConstraint("action IN ('confirm','edit','reject')", name="valid_review_action"),
    )
