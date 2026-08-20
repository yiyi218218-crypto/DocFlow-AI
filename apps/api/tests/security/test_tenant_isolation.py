"""Tenant isolation.

The single most important test file in this repository. It has two halves:

  * Structural tests parameterised over every table carrying WorkspaceScopedMixin,
    so a developer who adds a tenant table without an RLS policy gets a red build
    rather than shipping a data leak. New tables are covered automatically.

  * Data-level tests proving cross-tenant read, update, delete and insert are all
    denied through the real application role.

docs/10-architecture.md §17.3
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.models import tenant_scoped_tables

pytestmark = pytest.mark.integration

TENANT_TABLES = tenant_scoped_tables()


# --------------------------------------------------------------------------
# Structural — covers every current and future tenant table
# --------------------------------------------------------------------------


def test_model_registry_finds_tenant_tables() -> None:
    """Guard the guard: if this returns nothing, every test below vacuously passes."""
    assert len(TENANT_TABLES) >= 10, TENANT_TABLES
    assert "documents" in TENANT_TABLES
    assert "document_facts" in TENANT_TABLES


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_rls_is_enabled(admin_session: AsyncSession, table: str) -> None:
    enabled = await admin_session.scalar(
        text(
            "SELECT relrowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :t"
        ),
        {"t": table},
    )
    assert enabled is True, f"RLS is not enabled on {table}"


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_isolation_policy_exists(admin_session: AsyncSession, table: str) -> None:
    count = await admin_session.scalar(
        text("SELECT count(*) FROM pg_policies WHERE tablename = :t AND policyname = 'tenant_isolation'"),
        {"t": table},
    )
    assert count == 1, f"{table} has no tenant_isolation policy"


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_app_role_is_not_table_owner(admin_session: AsyncSession, table: str) -> None:
    """The invariant that makes RLS effective without FORCE.

    A table owner bypasses RLS unless FORCE is set. We deliberately do not FORCE
    (it would break cross-tenant cron jobs), so ownership is the load-bearing
    assumption — and therefore must be asserted, not assumed.
    """
    owner = await admin_session.scalar(
        text(
            "SELECT pg_get_userbyid(c.relowner) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :t"
        ),
        {"t": table},
    )
    assert owner != "docflow", (
        f"the application role owns {table} and therefore bypasses RLS — "
        "either change ownership or enable FORCE ROW LEVEL SECURITY"
    )


# --------------------------------------------------------------------------
# Data-level — the attacks themselves
# --------------------------------------------------------------------------


async def test_read_is_scoped_to_own_workspace(as_tenant, tenants) -> None:
    async with as_tenant(tenants.ws_a) as s:
        rows = (await s.execute(text("SELECT id FROM documents"))).scalars().all()
    assert tenants.doc_a in rows
    assert tenants.doc_b not in rows


async def test_cannot_read_other_tenant_by_direct_id(as_tenant, tenants) -> None:
    """The IDOR attack: guessing another tenant's UUID must return nothing."""
    async with as_tenant(tenants.ws_a) as s:
        found = await s.scalar(
            text("SELECT id FROM documents WHERE id = :id"), {"id": tenants.doc_b}
        )
    assert found is None


async def test_cannot_update_other_tenant(as_tenant, tenants) -> None:
    async with as_tenant(tenants.ws_a) as s:
        result = await s.execute(
            text("UPDATE documents SET title = 'pwned' WHERE id = :id"), {"id": tenants.doc_b}
        )
        assert result.rowcount == 0


async def test_cannot_delete_other_tenant(as_tenant, tenants) -> None:
    async with as_tenant(tenants.ws_a) as s:
        result = await s.execute(
            text("DELETE FROM tasks WHERE id = :id"), {"id": tenants.task_b}
        )
        assert result.rowcount == 0


async def test_cannot_insert_into_other_tenant(as_tenant, tenants) -> None:
    """WITH CHECK stops a forged workspace_id on write."""
    with pytest.raises(DBAPIError) as exc:
        async with as_tenant(tenants.ws_a) as s:
            await s.execute(
                text(
                    "INSERT INTO tasks (id, workspace_id, title, origin) "
                    "VALUES (:id, :ws, 'smuggled', 'user')"
                ),
                {"id": uuid.uuid4(), "ws": tenants.ws_b},
            )
    assert "row-level security" in str(exc.value).lower()


async def test_unscoped_session_sees_nothing(as_tenant, tenants) -> None:
    """A missing tenant context must fail closed, not open.

    nullif(current_setting(...), '')::uuid yields NULL, and `workspace_id = NULL`
    is never true — so an unscoped session sees zero rows rather than every row.
    """
    async with as_tenant(None) as s:
        rows = (await s.execute(text("SELECT id FROM documents"))).scalars().all()
    assert rows == []


async def test_task_visibility_follows_workspace(as_tenant, tenants) -> None:
    async with as_tenant(tenants.ws_b) as s:
        rows = (await s.execute(text("SELECT id FROM tasks"))).scalars().all()
    assert tenants.task_b in rows
    assert tenants.task_a not in rows


async def test_usage_records_are_append_only(as_tenant, tenants) -> None:
    """Cost history must not be rewritable by the application role."""
    async with as_tenant(tenants.ws_a) as s:
        await s.execute(
            text(
                "INSERT INTO usage_records (workspace_id, operation, provider, model_id, cost_usd) "
                "VALUES (:ws, 'test', 'anthropic', 'claude-haiku-4-5', 0.001)"
            ),
            {"ws": tenants.ws_a},
        )

    with pytest.raises(DBAPIError) as exc:
        async with as_tenant(tenants.ws_a) as s:
            await s.execute(text("UPDATE usage_records SET cost_usd = 0 WHERE operation = 'test'"))
    assert "permission denied" in str(exc.value).lower()
