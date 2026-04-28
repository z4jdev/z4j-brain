"""Alembic environment for the brain.

Reads the database URL from :class:`z4j_brain.settings.Settings`
(which itself reads ``Z4J_DATABASE_URL``) so there is exactly one
source of truth for the connection string.

Async engine via SQLAlchemy 2 + asyncpg. Migrations run inside an
async transaction. ``target_metadata`` points at
:attr:`z4j_brain.persistence.Base.metadata` - every model imported
into the brain's ORM tree is therefore eligible for autogenerate.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from z4j_brain.persistence import Base
from z4j_brain.settings import Settings

# B2 will register every model module here so Base.metadata is fully
# populated before autogenerate runs. In B1 there are no models yet,
# which is the expected state - `alembic upgrade head` is a no-op.

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Pull the URL from settings, never from alembic.ini."""
    settings = Settings()  # type: ignore[call-arg]
    return settings.database_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (URL string only, no engine).

    Useful for generating SQL files for review before applying. The
    URL still comes from settings, not from alembic.ini.
    """
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Sync callback executed inside the async connection."""
    # Round-6 audit fix Mig-HIGH-2 (Apr 2026): per-migration
    # transaction is required so individual migrations can opt into
    # ``op.get_context().autocommit_block()`` for ``CREATE INDEX
    # CONCURRENTLY`` and other statements that Postgres refuses
    # inside a transaction. Without this each ``with autocommit_block``
    # exits then re-enters the SAME enclosing transaction; CONCURRENTLY
    # still errors and silently downgrades to a blocking lock.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Configure and run migrations against the async engine."""
    config_section = config.get_section(config.config_ini_section, {}) or {}
    config_section["sqlalchemy.url"] = _resolve_database_url()

    connectable = async_engine_from_config(
        config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against the live database."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
