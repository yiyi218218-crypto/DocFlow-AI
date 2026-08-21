"""row level security

Enables Postgres RLS on every tenant-scoped table as a backstop underneath
application-level `WHERE workspace_id = ...` filtering.

Why both layers: app-level filtering alone fails the day someone adds an endpoint
and forgets one WHERE clause — the most common cause of real-world SaaS tenant
breaches. RLS turns that mistake into an empty result set instead of a data leak.
The app filter stays because it keeps queries index-friendly and intent explicit.

Why NOT `FORCE ROW LEVEL SECURITY`: tables are owned by the migration role
(`postgres`) and the application connects as a different, non-owner role
(`docflow`), so RLS already applies to every application query. Adding FORCE
would additionally subject the owner to RLS, which breaks cron jobs and backfills
that legitimately span tenants. The invariant that actually matters — "the app
role is never the table owner" — is asserted directly in
tests/security/test_tenant_isolation.py rather than papered over with FORCE.

Revision ID: 0002_row_level_security
Revises: 0001_initial_schema
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_row_level_security"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "docflow"

# Kept in sync with docflow.models.tenant_scoped_tables() by a test, so a new
# tenant table without a policy fails the build instead of leaking.
TENANT_TABLES = [
    "conversations",
    "document_chunk_embeddings",
    "document_chunks",
    "document_facts",
    "document_pages",
    "document_stages",
    "documents",
    "fact_reviews",
    "messages",
    "notifications",
    "task_comments",
    "tasks",
    "usage_records",
]

# Not tenant-scoped by a workspace_id column, but still needs the app role to be
# able to read/write them.
SHARED_TABLES = [
    "users",
    "workspaces",
    "memberships",
    "refresh_tokens",
    "audit_logs",
    "idempotency_keys",
    "embedding_cache",
    "usage_daily",
    "eval_runs",
    "eval_results",
]


def upgrade() -> None:
    # The app role must exist even on a fresh managed database where
    # infra/docker/init-db.sql never ran.
    # Create the role WITHOUT a password. A migration must never mint a
    # credential: the value would be identical in every environment, live in
    # version control forever, and managed providers reject it outright (Neon
    # returns "insecure password" for exactly this).
    #
    # The operator grants LOGIN and a password out of band:
    #     ALTER ROLE docflow WITH LOGIN PASSWORD '<generated>';
    # Locally, infra/docker/init-db.sql has already created the role with
    # credentials, so this DO block is a no-op. See docs/DEPLOYMENT.md.
    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN;
          END IF;
        END
        $$;
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # nullif(..., '') so an unset GUC yields NULL, and NULL = uuid is never
        # true — an unscoped session therefore sees zero rows rather than all rows.
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
              WITH CHECK (workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)
            """
        )

    # Append-only tables: the app may insert and read, never mutate history.
    op.execute(f"REVOKE UPDATE, DELETE ON usage_records FROM {APP_ROLE}")
    op.execute(f"REVOKE UPDATE, DELETE ON audit_logs FROM {APP_ROLE}")


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute(f"GRANT UPDATE, DELETE ON usage_records TO {APP_ROLE}")
    op.execute(f"GRANT UPDATE, DELETE ON audit_logs TO {APP_ROLE}")
