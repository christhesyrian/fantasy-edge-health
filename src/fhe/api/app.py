"""FastAPI application factory.

Shared resources are built once in the lifespan and torn down deterministically,
rather than being created lazily on first use where a failure would surface as a
confusing request error instead of a startup failure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fhe import __version__
from fhe.api.errors import register_error_handlers
from fhe.api.events import create_event_bus
from fhe.api.middleware import RequestContextMiddleware
from fhe.api.routers import health, simulations
from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.config import Settings, get_settings
from fhe.core.simulation import generate_player_pool
from fhe.db import create_engine, create_session_factory
from fhe.observability import configure_logging, get_logger

log = get_logger(__name__)

API_PREFIX = "/api/v1"

DESCRIPTION = """
Injury-adjusted fantasy football draft intelligence.

**Demo mode needs no credentials.** `POST /api/v1/simulations` starts a mock
draft against a deterministic synthetic player pool, and every board it returns
is produced by the same engine a live Sleeper draft uses.

Every score is decomposable: a recommendation's `components` always sum to its
`overall_score`, so the arithmetic behind a number is part of the response
rather than something to take on trust.

Availability risk is an estimate of fantasy availability from public injury
reports. It is not a medical prediction, and every health payload carries its
own `limitations`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build shared resources on startup and release them on shutdown."""
    settings: Settings = app.state.settings
    configure_logging(settings)

    for warning in settings.storage_warnings():
        log.warning("configuration_degraded", detail=warning)

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    event_bus = create_event_bus(settings.redis_url or None)
    app.state.event_bus = event_bus
    app.state.registry = DraftSessionRegistry(event_bus)

    # Generated once at startup: it is deterministic, costs a moment, and makes
    # demo mode instant on first request.
    app.state.demo_pool = generate_player_pool()
    log.info(
        "api_started",
        version=__version__,
        environment=settings.env.value,
        demo_pool_size=len(app.state.demo_pool),
        event_bus="redis" if event_bus.is_distributed else "in_process",
    )

    try:
        yield
    finally:
        await event_bus.aclose()
        await engine.dispose()
        log.info("api_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Args:
        settings: Override configuration. Tests pass their own rather than
            mutating the process environment.
    """
    resolved = settings or get_settings()

    app = FastAPI(
        title="Fantasy Health Edge API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = resolved

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        # Explicit origins only. A wildcard alongside credentials would let any
        # site read authenticated responses.
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type", "x-request-id"],
        expose_headers=["x-request-id"],
        max_age=600,
    )

    register_error_handlers(app)

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(simulations.router, prefix=API_PREFIX)

    return app


app = create_app()
