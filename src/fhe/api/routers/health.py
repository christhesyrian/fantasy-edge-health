"""Liveness, readiness, and metrics.

Liveness and readiness are deliberately different questions. Liveness asks "is
this process running?" and must never touch a dependency, or a database blip
would make an orchestrator kill a perfectly healthy container. Readiness asks
"should traffic be sent here?" and does check dependencies.

Both surface active degradations, so a SQLite-and-in-process-bus deployment can
never be mistaken for a production one.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from fhe.api.deps import EventBusDep, RegistryDep, SessionDep, SettingsDep
from fhe.api.schemas import HealthStatus
from fhe.observability import REGISTRY, get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


def _degradations(settings: SettingsDep, event_bus_distributed: bool) -> list[str]:
    """Every active fallback, in plain language."""
    warnings = list(settings.storage_warnings())
    if not event_bus_distributed:
        warnings.append(
            "Draft events use an in-process bus: sessions and live updates do "
            "not span API processes."
        )
    return warnings


@router.get("/health", response_model=HealthStatus, summary="Liveness")
async def liveness(settings: SettingsDep, event_bus: EventBusDep) -> HealthStatus:
    """Report that the process is up.

    Touches no dependency on purpose: a database outage should not cause an
    orchestrator to restart an otherwise healthy container.
    """
    from fhe import __version__

    return HealthStatus(
        status="ok",
        version=__version__,
        environment=settings.env.value,
        checks={"process": "ok"},
        degradations=_degradations(settings, event_bus.is_distributed),
    )


@router.get("/health/ready", response_model=HealthStatus, summary="Readiness")
async def readiness(
    settings: SettingsDep,
    session: SessionDep,
    event_bus: EventBusDep,
    registry: RegistryDep,
    response: Response,
) -> HealthStatus:
    """Report whether this instance should receive traffic."""
    checks: dict[str, str] = {}
    healthy = True

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as error:  # noqa: BLE001 - readiness reports, never raises
        # A readiness probe must answer, not propagate: an exception here would
        # become a 500 that says nothing about which dependency failed.
        checks["database"] = f"error: {type(error).__name__}"
        healthy = False

    checks["event_bus"] = "redis" if event_bus.is_distributed else "in_process"
    checks["active_sessions"] = str(registry.count)

    from fhe import __version__

    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthStatus(
        status="ready" if healthy else "not_ready",
        version=__version__,
        environment=settings.env.value,
        checks=checks,
        degradations=_degradations(settings, event_bus.is_distributed),
    )


@router.get("/metrics", include_in_schema=False, summary="Prometheus metrics")
async def metrics() -> Response:
    """Expose metrics in the Prometheus text format."""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
