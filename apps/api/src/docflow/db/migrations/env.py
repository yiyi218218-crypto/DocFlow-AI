"""Alembic environment.

Runs against the async engine so there is exactly one Postgres driver (asyncpg)
across the app, the worker and migrations. A second sync driver is one more thing
to install, pin and have subtly disagree about type handling.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from pgvector.sqlalchemy import Vector
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the package registers every table on Base.metadata.
import docflow.models  # noqa: F401
from docflow.core.config import settings
from docflow.core.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations run as the admin role, which must be able to bypass RLS.
config.set_main_option("sqlalchemy.url", settings.database_admin_url)

target_metadata = Base.metadata

# Types the extensions own. Alembic must never try to manage them.
EXTENSION_TABLES = {"vector", "spatial_ref_sys"}


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    return not (type_ == "table" and name in EXTENSION_TABLES)


def render_item(type_, obj, autogen_context):  # type: ignore[no-untyped-def]
    """Teach autogenerate to emit pgvector's Vector type with its import."""
    if type_ == "type" and isinstance(obj, Vector):
        autogen_context.imports.add("from pgvector.sqlalchemy import Vector")
        return f"Vector(dim={obj.dim})"
    return False


CONFIGURE_KWARGS = {
    "target_metadata": target_metadata,
    "compare_type": True,
    "compare_server_default": True,
    "include_object": include_object,
    "render_item": render_item,
}


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONFIGURE_KWARGS,  # type: ignore[arg-type]
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **CONFIGURE_KWARGS)  # type: ignore[arg-type]
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
