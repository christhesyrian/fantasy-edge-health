"""Request-scoped middleware: correlation ids, access logging, and the gate."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Final

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from fhe.api.services.access import COOKIE_NAME, token_is_valid
from fhe.config import Settings
from fhe.observability import get_logger

log = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"

# Paths reachable without the shared password.
#
# Health and metrics are open because a deployment platform probes them before
# it could ever hold a cookie, and a gated health check reads as a dead service
# and gets restarted forever. Neither exposes player data or accepts a change;
# what they do expose is the degradation list, which is a deliberate trade for
# being able to deploy at all.
_OPEN_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "/api/v1/health",
    "/api/v1/metrics",
    # The gate's own endpoints, or there would be no way to pass it.
    "/api/v1/auth/",
)


def _is_open_path(path: str) -> bool:
    """Whether a path is reachable without a session."""
    return any(path.startswith(prefix) for prefix in _OPEN_PATH_PREFIXES)


class AccessGateMiddleware(BaseHTTPMiddleware):
    """Requires the shared password on everything but the open paths.

    A middleware rather than a router dependency, deliberately: a dependency has
    to be remembered on every new route, and the failure of forgetting one is an
    endpoint that is quietly public. Here a new route is closed by default and
    opening it is the thing that takes an edit.
    """

    def __init__(self, app: object, settings: Settings) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Pass the request through, or refuse it with a 401."""
        if not self._settings.access_enabled or _is_open_path(request.url.path):
            return await call_next(request)

        # A CORS preflight carries no cookies by design, so refusing it would
        # break the browser's ability to make the real, authenticated request.
        # It reveals nothing: the response is a set of allowed methods.
        if request.method == "OPTIONS":
            return await call_next(request)

        if token_is_valid(self._settings, request.cookies.get(COOKIE_NAME)):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={
                "error": "not_authenticated",
                "detail": "This instance is password protected. Sign in to continue.",
                "request_id": getattr(request.state, "request_id", None),
            },
        )


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id and emit one structured access log per request.

    An inbound ``x-request-id`` is honoured so a trace survives a proxy hop;
    otherwise one is minted. The id is bound into the structlog context, so
    every log line emitted while handling the request carries it without any
    handler having to pass it around.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Run the request with a bound correlation id."""
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            log.error(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round((perf_counter() - started) * 1000, 2),
                error_category=type(error).__name__,
            )
            raise

        duration_ms = round((perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        # The event stream is long-lived; logging its duration on completion
        # would be misleading, so it is logged as opened instead.
        if request.url.path.endswith("/events"):
            log.info("stream_closed", path=request.url.path, duration_ms=duration_ms)
        else:
            log.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response
