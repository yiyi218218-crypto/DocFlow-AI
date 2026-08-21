"""Document, page and fact response contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from docflow.models.enums import (
    CitationStatus,
    ConfidenceBucket,
    DocumentStatus,
    DocumentType,
    FactKind,
    Language,
    PipelineStage,
    ReviewStatus,
    StageStatus,
    TextExtractionMethod,
)
from docflow.schemas.common import APIModel


class StageOut(APIModel):
    stage: PipelineStage
    status: StageStatus
    attempt: int
    duration_ms: int | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class DocumentSummary(APIModel):
    id: uuid.UUID
    original_filename: str
    title: str | None = None
    mime_type: str
    size_bytes: int
    page_count: int | None = None
    language: Language
    document_type: DocumentType | None = None
    status: DocumentStatus
    error_code: str | None = None
    security_flags: list[str] = Field(default_factory=list)
    created_at: datetime
    processing_completed_at: datetime | None = None

    # Denormalised for the library view, so rendering a list of 40 documents is
    # one query rather than 40.
    fact_count: int = 0
    deadline_count: int = 0
    next_deadline_at: datetime | None = None


class DocumentDetail(DocumentSummary):
    summary: str | None = None
    organization: str | None = None
    reference_number: str | None = None
    published_date: datetime | None = None
    total_cost_usd: float = 0.0
    stages: list[StageOut] = Field(default_factory=list)

    @property
    def progress(self) -> float:
        if not self.stages:
            return 0.0
        done = sum(1 for s in self.stages if s.status is StageStatus.COMPLETED)
        return round(done / len(self.stages), 2)


class Rect(BaseModel):
    """Normalised to [0,1] against the page display box.

    The browser multiplies by its rendered viewport and does nothing else — no
    rotation maths, no DPI assumptions. See docs/40-frontend.md §14.5.
    """

    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class FactOut(APIModel):
    id: uuid.UUID
    kind: FactKind
    payload: dict[str, Any]

    source_page: int
    source_quote: str
    source_rects: list[Rect] = Field(default_factory=list)
    citation_status: CitationStatus
    citation_match_score: float | None = None

    confidence_bucket: ConfidenceBucket
    confidence: float = Field(..., alias="confidence_calibrated")

    due_at: datetime | None = None
    normalization_status: str | None = None
    is_primary: bool = False
    review_status: ReviewStatus

    @property
    def is_highlightable(self) -> bool:
        return bool(self.source_rects)


class PageOut(APIModel):
    page_number: int
    width: float
    height: float
    rotation: int
    extraction_method: TextExtractionMethod
    ocr_confidence: float | None = None
    citation_granularity: str
    image_url: str | None = None


class DocumentAnalysisOut(BaseModel):
    """One request for everything the detail page needs."""

    document: DocumentDetail
    facts: list[FactOut]
    pages: list[PageOut]
    counts_by_kind: dict[str, int]
    confidence_summary: dict[str, int]
    citation_verification_rate: float | None = None
    low_confidence_hidden: int = 0


class UploadAccepted(BaseModel):
    id: uuid.UUID
    status: DocumentStatus
    original_filename: str
    size_bytes: int
    created_at: datetime
    poll_url: str
    duplicate_of: uuid.UUID | None = None


class FactReviewIn(BaseModel):
    action: str = Field(..., pattern="^(confirm|reject)$")
    note: str | None = Field(None, max_length=1000)


class FactUpdateIn(BaseModel):
    payload: dict[str, Any] | None = None
    due_at: datetime | None = None
    note: str | None = Field(None, max_length=1000)
