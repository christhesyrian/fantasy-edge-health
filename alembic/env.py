"""Alembic environment.

The database URL comes from application settings rather than ``alembic.ini``, so
there is exactly one place a connection string is configured and no credential
ever lands in a committed file.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

import fhe.db.models  # noqa: F401  -- imported for the side effect of registering tables
from fhe.config import get_settings
from fhe.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Autogenerate compares this metadata against the live database.
target_metadata = Base.metadata

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)


def _configure(connection: Connection) -> None:
    """Shared configuration for online migrations.

    ``render_as_batch`` is required on SQLite, which cannot ALTER most columns
    and needs Alembic's copy-and-move batch mode instead.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )


def run_migrations_offline() -> None:
    """Emit SQL without a live connection."""
    context.configure(
        url=settings.sqlalchemy_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on an established connection."""
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async engine and run migrations through it."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
