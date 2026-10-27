"""Domain enumerations.

Native Postgres ENUMs for stable small sets. `fact_kind` is the one that grows:
adding a value is a single ALTER TYPE, and — because facts live in one table with a
JSONB payload (docs/30-data-api.md §12.3) — a new fact kind needs no migration of
its own. That is what makes a new document vertical cheap.
"""

from __future__ import annotations

from enum import StrEnum


class WorkspaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    QUARANTINED = "quarantined"

    @property
    def is_terminal(self) -> bool:
        return self in (
            DocumentStatus.COMPLETED,
            DocumentStatus.PARTIAL,
            DocumentStatus.FAILED,
            DocumentStatus.QUARANTINED,
        )


class PipelineStage(StrEnum):
    """Ordered. The orchestrator advances to the first stage that is not completed."""

    VALIDATING = "validating"
    NORMALIZING = "normalizing"
    PARSING = "parsing"
    OCR = "ocr"
    SEGMENTING = "segmenting"
    CLASSIFYING = "classifying"
    EXTRACTING = "extracting"
    ALIGNING = "aligning"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"

    @classmethod
    def ordered(cls) -> list[PipelineStage]:
        return [
            cls.VALIDATING,
            cls.NORMALIZING,
            cls.PARSING,
            cls.OCR,
            cls.SEGMENTING,
            cls.CLASSIFYING,
            cls.EXTRACTING,
            cls.ALIGNING,
            cls.CHUNKING,
            cls.EMBEDDING,
            cls.INDEXING,
        ]

    @property
    def position(self) -> int:
        return self.ordered().index(self)


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class DocumentType(StrEnum):
    UNIVERSITY_CIRCULAR = "university_circular"
    SCHOLARSHIP_NOTICE = "scholarship_notice"
    ADMISSION_NOTICE = "admission_notice"
    EXAM_NOTICE = "exam_notice"
    GOVERNMENT_NOTICE = "government_notice"
    JOB_CIRCULAR = "job_circular"
    INVOICE = "invoice"
    CONTRACT = "contract"
    OTHER = "other"


class Language(StrEnum):
    EN = "en"
    BN = "bn"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class FactKind(StrEnum):
    DEADLINE = "deadline"
    REQUIREMENT = "requirement"
    ACTION_ITEM = "action_item"
    ENTITY = "entity"
    CONTACT = "contact"
    LINK = "link"
    RISK = "risk"
    DECISION = "decision"


class CitationStatus(StrEnum):
    """How well the model's quote aligned to the source text.

    `unverified` facts are never shown as confirmed and never generate tasks.
    """

    VERIFIED = "verified"
    PARTIAL = "partial"
    UNVERIFIED = "unverified"
    PAGE_ONLY = "page_only"  # OCR tier lost coordinates; honest degradation


class ConfidenceBucket(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    EDITED = "edited"
    REJECTED = "rejected"


class ExtractionMethod(StrEnum):
    LLM_EXTRACTION = "llm_extraction"
    RULE = "rule"
    USER = "user"


class TaskStatus(StrEnum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class TaskOrigin(StrEnum):
    AI = "ai"
    USER = "user"


class NotificationType(StrEnum):
    DEADLINE_REMINDER = "deadline_reminder"
    DOCUMENT_COMPLETED = "document_completed"
    DOCUMENT_FAILED = "document_failed"
    TASK_ASSIGNED = "task_assigned"
    WEEKLY_DIGEST = "weekly_digest"


class TextExtractionMethod(StrEnum):
    """Which OCR tier produced a page. Feeds the confidence composite."""

    NATIVE = "native"
    OCR_TESSERACT = "ocr_tesseract"
    OCR_CLOUD = "ocr_cloud"
    OCR_VLM = "ocr_vlm"
    NONE = "none"


class NormalizationStatus(StrEnum):
    OK = "ok"
    AMBIGUOUS = "ambiguous"
    RELATIVE_UNRESOLVED = "relative_unresolved"
    IMPLAUSIBLE = "implausible"
    FAILED = "failed"


class ModelTier(StrEnum):
    """Call sites ask for a tier, never a model name.

    Model routing, provider migration and cost experiments become configuration.
    """

    FAST = "fast"
    BALANCED = "balanced"
    DEEP = "deep"
