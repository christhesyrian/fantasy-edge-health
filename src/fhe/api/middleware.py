"""Request-scoped middleware: correlation ids and access logging."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from time import perf_counter

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from fhe.observability import get_logger

log = get_logger(__name__)

REQUEST_ID_HEADER = "x-request-id"


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
