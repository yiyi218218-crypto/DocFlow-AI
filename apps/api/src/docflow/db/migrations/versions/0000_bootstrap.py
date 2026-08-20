"""bootstrap: extensions and uuidv7

Must run before the initial schema: every table's primary key uses
`server_default=text("uuidv7()")`, and Postgres validates a default expression at
DDL time, so the function has to exist first.

This lives in a migration rather than only in infra/docker/init-db.sql because
init-db.sql runs exclusively on a fresh local Docker volume. A managed Postgres
(Neon, RDS, Supabase) never executes it, so a production `alembic upgrade head`
would fail on a missing function. Anything the schema depends on belongs here.

uuidv7 gives time-ordered keys — good index locality, no page splits — while
staying non-enumerable, so ids are safe to expose in URLs. Sequential integers
invite IDOR probing and leak business volume.

Revision ID: 0000_bootstrap
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0000_bootstrap"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # Postgres 18 ships uuidv7() natively; this shim covers 14–17.
    # Built on gen_random_uuid(), which is core since PG13 — deliberately NOT on
    # pgcrypto's gen_random_bytes(), which many managed providers do not enable.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION uuidv7() RETURNS uuid AS $$
        DECLARE
          ts_ms bytea;
          b bytea;
        BEGIN
          -- 48-bit big-endian millisecond timestamp
          ts_ms := substring(
            int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3
          );
          b := uuid_send(gen_random_uuid());
          b := ts_ms || substring(b FROM 7 FOR 10);
          b := set_byte(b, 6, (b'0111' || get_byte(b, 6)::bit(4))::bit(8)::int);  -- version 7
          b := set_byte(b, 8, (b'10'   || get_byte(b, 8)::bit(6))::bit(8)::int);  -- variant 10xx
          RETURN encode(b, 'hex')::uuid;
        END
        $$ LANGUAGE plpgsql VOLATILE;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS uuidv7()")
    # Extensions are intentionally left in place: other schemas in the same
    # database may depend on them, and dropping vector would destroy indexes.
