"""Request-scoped dependencies.

Shared objects (settings, engine, event bus, session registry) are built once in
the lifespan and stored on ``app.state``, then handed to routes through these
accessors. That keeps construction out of request handlers and makes every
dependency trivially replaceable in a test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.api.events import EventBus
from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.config import Settings
from fhe.core.draft.models import DraftablePlayer


def get_settings_dep(request: Request) -> Settings:
    """Application settings."""
    settings: Settings = request.app.state.settings
    return settings


def get_event_bus(request: Request) -> EventBus:
    """The draft event bus."""
    bus: EventBus = request.app.state.event_bus
    return bus


def get_registry(request: Request) -> DraftSessionRegistry:
    """The draft session registry."""
    registry: DraftSessionRegistry = request.app.state.registry
    return registry


def get_demo_pool(request: Request) -> tuple[DraftablePlayer, ...]:
    """The deterministic synthetic player pool used by demo mode."""
    pool: tuple[DraftablePlayer, ...] = request.app.state.demo_pool
    return pool


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A database session for the duration of a request.

    Commits on success and rolls back on failure, so a handler never leaves a
    half-applied transaction behind.
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
EventBusDep = Annotated[EventBus, Depends(get_event_bus)]
RegistryDep = Annotated[DraftSessionRegistry, Depends(get_registry)]
DemoPoolDep = Annotated[tuple[DraftablePlayer, ...], Depends(get_demo_pool)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
