from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from docflow.core.config import settings
from docflow.core.database import set_tenant_context

# --- engines --------------------------------------------------------------
# Two engines on purpose: `admin` owns the tables and bypasses RLS (it is the
# migration role), `app` is the non-owner role the application actually uses and
# is therefore subject to every policy. Testing RLS through the admin engine
# would prove nothing.


# Engines are function-scoped and use NullPool. asyncpg connections are bound to
# the event loop that created them, and pytest-asyncio gives each test a fresh
# loop — a session-scoped engine therefore hands out connections attached to a
# dead loop. Per-test engines are marginally slower and completely reliable.


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def _reset_app_engine():  # type: ignore[no-untyped-def]
    """Detach the application's module-level engine from the previous test's loop.

    docflow.core.database.engine is created once at import and pools asyncpg
    connections, but each test runs in a fresh event loop — so a pooled
    connection from the last test belongs to a loop that is already closed.

    dispose(close=False) abandons those connections without awaiting on the dead
    loop; the next checkout opens fresh ones in the current loop.
    """
    from docflow.core import database

    await database.engine.dispose(close=False)
    if database._SystemSessionFactory is not None:
        await database._system_engine.dispose(close=False)
    yield
    await database.engine.dispose(close=False)
    if database._SystemSessionFactory is not None:
        await database._system_engine.dispose(close=False)


@pytest_asyncio.fixture
async def admin_engine():  # type: ignore[no-untyped-def]
    engine = create_async_engine(settings.database_admin_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def app_engine():  # type: ignore[no-untyped-def]
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_session(admin_engine) -> AsyncIterator[AsyncSession]:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(admin_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def app_session_factory(app_engine):  # type: ignore[no-untyped-def]
    return async_sessionmaker(app_engine, class_=AsyncSession, expire_on_commit=False)


# --- fixture data ---------------------------------------------------------


class TenantFixture:
    """Two isolated workspaces with one document and one task each."""

    def __init__(self) -> None:
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        self.ws_a = uuid.uuid4()
        self.ws_b = uuid.uuid4()
        self.doc_a = uuid.uuid4()
        self.doc_b = uuid.uuid4()
        self.task_a = uuid.uuid4()
        self.task_b = uuid.uuid4()


@pytest_asyncio.fixture
async def tenants(admin_session: AsyncSession) -> AsyncIterator[TenantFixture]:
    fx = TenantFixture()
    suffix = uuid.uuid4().hex[:8]

    for user, ws, doc, task, tag in (
        (fx.user_a, fx.ws_a, fx.doc_a, fx.task_a, "a"),
        (fx.user_b, fx.ws_b, fx.doc_b, fx.task_b, "b"),
    ):
        await admin_session.execute(
            text("INSERT INTO users (id, email, full_name) VALUES (:id, :email, :name)"),
            {"id": user, "email": f"{tag}-{suffix}@example.test", "name": f"User {tag.upper()}"},
        )
        await admin_session.execute(
            text(
                "INSERT INTO workspaces (id, name, slug, owner_id) "
                "VALUES (:id, :name, :slug, :owner)"
            ),
            {"id": ws, "name": f"WS {tag}", "slug": f"ws-{tag}-{suffix}", "owner": user},
        )
        await admin_session.execute(
            text(
                "INSERT INTO memberships (workspace_id, user_id, role) VALUES (:ws, :user, 'owner')"
            ),
            {"ws": ws, "user": user},
        )
        await admin_session.execute(
            text(
                "INSERT INTO documents (id, workspace_id, uploaded_by, original_filename, "
                "mime_type, size_bytes, content_sha256, storage_key_original, status) "
                "VALUES (:id, :ws, :user, :fn, 'application/pdf', 1024, :sha, :key, 'completed')"
            ),
            {
                "id": doc,
                "ws": ws,
                "user": user,
                "fn": f"circular-{tag}.pdf",
                "sha": uuid.uuid4().hex + uuid.uuid4().hex,
                "key": f"ws/{ws}/doc/{doc}/original.pdf",
            },
        )
        await admin_session.execute(
            text(
                "INSERT INTO tasks (id, workspace_id, document_id, title, origin, created_by) "
                "VALUES (:id, :ws, :doc, :title, 'user', :user)"
            ),
            {"id": task, "ws": ws, "doc": doc, "title": f"Task {tag}", "user": user},
        )

    await admin_session.commit()
    yield fx

    for table, ids in (
        ("tasks", (fx.task_a, fx.task_b)),
        ("documents", (fx.doc_a, fx.doc_b)),
        ("workspaces", (fx.ws_a, fx.ws_b)),
        ("users", (fx.user_a, fx.user_b)),
    ):
        await admin_session.execute(
            text(f"DELETE FROM {table} WHERE id = ANY(:ids)"), {"ids": list(ids)}
        )
    await admin_session.commit()


@pytest_asyncio.fixture
async def as_tenant(app_session_factory):  # type: ignore[no-untyped-def]
    """Open a transaction as the application role, scoped to one workspace."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scoped(workspace_id: uuid.UUID | str | None, user_id: uuid.UUID | None = None):  # type: ignore[no-untyped-def]
        async with app_session_factory() as session, session.begin():
            await set_tenant_context(session, workspace_id, user_id)
            yield session

    return _scoped
