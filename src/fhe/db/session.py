"""Async engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from fhe.config import Settings
from fhe.observability import get_logger

log = get_logger(__name__)


def create_engine(settings: Settings) -> AsyncEngine:
    """Build the async engine for the resolved database URL.

    SQLite gets ``check_same_thread=False`` because the async driver hands
    connections between threads; PostgreSQL gets a real connection pool.
    """
    url = settings.sqlalchemy_url
    if url.startswith("sqlite"):
        return create_async_engine(url, echo=False, connect_args={"check_same_thread": False})
    return create_async_engine(
        url,
        echo=False,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,  # a draft-night connection reset must not surface as a 500
        pool_recycle=1800,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to an engine."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Provide a transactional session scope.

    Commits on success, rolls back on any exception, and always closes. The
    exception is re-raised rather than swallowed.
    """
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
