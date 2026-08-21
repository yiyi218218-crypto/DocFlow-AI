"""Users, workspaces, memberships, refresh tokens.

`workspace` is THE tenant. There is deliberately no `organizations` table:
two tenancy levels on day one costs a join, a second permission surface and a
concept users cannot explain. Adding organizations above workspaces when billing
arrives is an additive migration. docs/10-architecture.md §17.1
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from docflow.core.database import Base
from docflow.models.enums import WorkspaceRole
from docflow.models.mixins import (
    SoftDeleteMixin,
    TimestampMixin,
    pg_enum,
    uuid_fk,
    uuid_pk,
)

if TYPE_CHECKING:
    from docflow.models.document import Document


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)  # null = OAuth only
    full_name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(Text)

    email_verified_at: Mapped[datetime | None]
    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Dhaka", server_default="Asia/Dhaka"
    )
    locale: Mapped[str] = mapped_column(String(8), default="en", server_default="en")

    oauth_provider: Mapped[str | None] = mapped_column(String(32))
    oauth_subject: Mapped[str | None] = mapped_column(String(255))

    # Lockout state (T13). Reset on a successful login.
    failed_login_count: Mapped[int] = mapped_column(default=0, server_default="0")
    locked_until: Mapped[datetime | None]
    last_login_at: Mapped[datetime | None]

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("oauth_provider", "oauth_subject", name="uq_users_oauth_identity"),
    )

    @property
    def is_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > datetime.now(
            self.locked_until.tzinfo
        )


class Workspace(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = uuid_fk("users.id", ondelete="RESTRICT")

    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Dhaka", server_default="Asia/Dhaka"
    )
    plan: Mapped[str] = mapped_column(String(32), default="free", server_default="free")

    # Feature flags and per-workspace overrides live here rather than in a
    # separate service — LaunchDarkly for a solo project is not a good trade.
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default="{}")

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = uuid_fk("workspaces.id")
    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    role: Mapped[WorkspaceRole] = mapped_column(
        pg_enum(WorkspaceRole, "workspace_role"),
        default=WorkspaceRole.MEMBER,
        nullable=False,
    )
    invited_by: Mapped[uuid.UUID | None] = uuid_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship(back_populates="memberships", foreign_keys=[user_id])
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_memberships_workspace_user"),
        Index("ix_memberships_user_workspace", "user_id", "workspace_id"),
    )


class RefreshToken(Base):
    """Rotating refresh tokens with family-based reuse detection.

    Every token descended from one login shares a `family_id`. Presenting a token
    that has already been rotated means it was stolen (or replayed), so the whole
    family is revoked. docs/50-security-ops.md §16.3 T13
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = uuid_fk("users.id")
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    rotated_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(String(64))

    user_agent: Mapped[str | None] = mapped_column(String(300))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.rotated_at is None
