"""blob store

Optional Postgres-backed object storage, used when STORAGE_BACKEND=postgres.

Created unconditionally because an empty table costs nothing, and a migration
that only sometimes runs is a migration that eventually diverges between
environments.

Deliberately NOT tenant-scoped with RLS: keys are already tenant-prefixed
(`ws/{workspace}/doc/{document}/...`), access goes through the API which
authorises first, and the cron/purge paths need to reach every tenant's blobs.
Making it RLS-scoped would force every storage write to carry a tenant GUC,
including ones that legitimately do not have one.

Revision ID: 0003_blob_store
Revises: 0002_row_level_security
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_blob_store"
down_revision: str | None = "0002_row_level_security"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blob_store",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Prefix scans (`ws/{id}/doc/{id}/%`) drive listing and purging.
    op.execute("CREATE INDEX ix_blob_store_key_prefix ON blob_store (key text_pattern_ops)")

    # Binary columns compress poorly and PDFs are already compressed; storing
    # them EXTERNAL skips a pointless compression pass on every write.
    op.execute("ALTER TABLE blob_store ALTER COLUMN data SET STORAGE EXTERNAL")

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON blob_store TO docflow")


def downgrade() -> None:
    op.drop_table("blob_store")
