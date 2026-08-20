"""Shared column mixins.

`workspace_id` is denormalised onto every tenant table even where it is derivable
via a join: RLS policies must be single-table predicates, and it keeps every index
tenant-prefixed. docs/30-data-api.md §12.2
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column


def pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Native Postgres ENUM storing the *values*, not the member names."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        create_constraint=False,
        values_callable=lambda e: [m.value for m in e],
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
        default=uuid.uuid4,
    )


def uuid_fk(target: str, *, ondelete: str = "CASCADE", nullable: bool = False, index: bool = True) -> Any:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
        index=index,
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkspaceScopedMixin:
    """Marker + column for every tenant-scoped table.

    tests/security/test_tenant_isolation.py enumerates subclasses of this mixin,
    so a new table is covered by the isolation suite automatically.
    """

    @declared_attr
    @classmethod
    def workspace_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class SoftDeleteMixin:
    """Only documents, tasks and workspaces get this.

    Everywhere else it is dead weight and a permanent source of forgotten
    `deleted_at IS NULL` predicates.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
