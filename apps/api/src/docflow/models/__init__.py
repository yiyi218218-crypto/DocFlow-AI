"""SQLAlchemy models.

Importing this package registers every table on Base.metadata, which is what
Alembic autogenerate and the tenant-isolation test suite both walk.
"""

from docflow.core.database import Base
from docflow.models.document import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentFact,
    DocumentPage,
    DocumentStage,
    FactReview,
)
from docflow.models.identity import Membership, RefreshToken, User, Workspace
from docflow.models.mixins import WorkspaceScopedMixin
from docflow.models.ops import (
    AuditLog,
    Conversation,
    EmbeddingCache,
    EvalResult,
    EvalRun,
    IdempotencyKey,
    Message,
    UsageDaily,
    UsageRecord,
)
from docflow.models.work import Notification, Task, TaskComment

__all__ = [
    "AuditLog",
    "Base",
    "ChunkEmbedding",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentFact",
    "DocumentPage",
    "DocumentStage",
    "EmbeddingCache",
    "EvalResult",
    "EvalRun",
    "FactReview",
    "IdempotencyKey",
    "Membership",
    "Message",
    "Notification",
    "RefreshToken",
    "Task",
    "TaskComment",
    "UsageDaily",
    "UsageRecord",
    "User",
    "Workspace",
    "WorkspaceScopedMixin",
    "tenant_scoped_tables",
]


def tenant_scoped_tables() -> list[str]:
    """Every table that must be covered by RLS.

    Derived from the mixin rather than hand-maintained, so a developer who adds a
    tenant table without an RLS policy gets a red build from
    tests/security/test_tenant_isolation.py instead of shipping a data leak.
    """
    return sorted(
        model.__tablename__
        for model in Base.registry._class_registry.values()  # noqa: SLF001
        if isinstance(model, type)
        and issubclass(model, WorkspaceScopedMixin)
        and hasattr(model, "__tablename__")
    )
