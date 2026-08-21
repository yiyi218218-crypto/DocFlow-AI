"""Async SQLAlchemy engine, session, and the tenant-context contract.

The tenant model is app-level filtering *plus* Postgres RLS (docs/10-architecture.md §17.2).
RLS reads `app.workspace_id` from the session. It must be set with SET LOCAL inside an
explicit transaction — SET (without LOCAL) leaks across pooled connections.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, mapped_column

from docflow.core.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with a deterministic constraint naming convention.

    Without this, Alembic autogenerate produces unnamed constraints that cannot
    be dropped in a downgrade.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Every timestamp is timestamptz. Never a naive `timestamp`.
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[dict[str, Any]]: JSONB,
    }


def uuid_pk() -> Any:
    from sqlalchemy.dialects.postgresql import UUID

    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("uuidv7()"),
        default=uuid.uuid4,
    )


engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionFactory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def set_tenant_context(
    session: AsyncSession,
    workspace_id: uuid.UUID | str | None,
    user_id: uuid.UUID | str | None = None,
) -> None:
    """Bind the RLS GUCs for the current transaction.

    Uses set_config(..., is_local => true) rather than literal SET LOCAL so the
    values are bound parameters — a workspace id can never be injected as SQL.
    """
    await session.execute(
        text("SELECT set_config('app.workspace_id', :ws, true)"),
        {"ws": str(workspace_id) if workspace_id else ""},
    )
    await session.execute(
        text("SELECT set_config('app.user_id', :uid, true)"),
        {"uid": str(user_id) if user_id else ""},
    )


@asynccontextmanager
async def tenant_session(
    workspace_id: uuid.UUID | str | None,
    user_id: uuid.UUID | str | None = None,
) -> AsyncIterator[AsyncSession]:
    """A transaction with tenant context bound. The only way workers touch tenant data."""
    async with SessionFactory() as session, session.begin():
        await set_tenant_context(session, workspace_id, user_id)
        yield session


_system_engine: Any = None
_SystemSessionFactory: Any = None


def _get_system_factory() -> Any:
    """Lazily build the elevated engine, so importing this module never opens a
    connection the process may not need."""
    global _system_engine, _SystemSessionFactory
    if _SystemSessionFactory is None:
        _system_engine = create_async_engine(
            settings.database_admin_url,
            echo=settings.db_echo,
            pool_size=3,
            max_overflow=2,
            pool_pre_ping=True,
        )
        _SystemSessionFactory = async_sessionmaker(
            _system_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
        )
    return _SystemSessionFactory


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncSession]:
    """Cross-tenant access for cron jobs and the usage ledger.

    Connects as the owner/migration role, which is not subject to RLS. The
    ordinary application role would give a session with no tenant GUC — which
    correctly sees zero rows and fails every INSERT with a WITH CHECK violation.
    Retention sweeps, reminder scheduling and cost accounting legitimately span
    tenants, so they get the elevated role deliberately, in one greppable place.
    """
    async with _get_system_factory()() as session, session.begin():
        yield session


async def check_database() -> bool:
    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def assert_transaction_pooling() -> None:
    """Guard against PgBouncer running in session mode.

    In session mode SET LOCAL leaks across requests and the whole tenant model
    silently degrades to 'no isolation'. Fail loudly at startup instead.
    """
    async with SessionFactory() as session:
        async with session.begin():
            await set_tenant_context(session, "00000000-0000-0000-0000-000000000000")
        async with session.begin():
            result = await session.execute(text("SELECT current_setting('app.workspace_id', true)"))
            leaked = result.scalar()
    if leaked:
        raise RuntimeError(
            "Tenant GUC leaked across transactions — the pooler is in session mode. "
            "RLS isolation cannot be trusted. Set pool_mode=transaction."
        )
