"""Error mapping.

Domain errors become HTTP responses in exactly one place. Handlers never build
error bodies themselves, so every failure looks the same to a client and no
handler can accidentally leak an internal message.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from fhe.api.services.draft_session import SessionNotFoundError
from fhe.core.errors import (
    DomainError,
    DraftStateError,
    LeagueConfigurationError,
    UnknownPlayerError,
)
from fhe.data.ingest.csv_import import CsvImportError
from fhe.data.providers.base import (
    ProviderDataError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from fhe.observability import get_logger

log = get_logger(__name__)


def _body(request: Request, error: str, detail: str) -> dict[str, str | None]:
    """Uniform error payload, carrying the request id for correlation."""
    return {
        "error": error,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }


def register_error_handlers(app: FastAPI) -> None:
    """Attach every domain-to-HTTP mapping."""

    @app.exception_handler(SessionNotFoundError)
    async def _session_missing(request: Request, exc: SessionNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body(request, "session_not_found", str(exc)),
        )

    @app.exception_handler(UnknownPlayerError)
    async def _unknown_player(request: Request, exc: UnknownPlayerError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body(request, "unknown_player", str(exc)),
        )

    @app.exception_handler(LeagueConfigurationError)
    async def _bad_league(request: Request, exc: LeagueConfigurationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_body(request, "invalid_league_configuration", str(exc)),
        )

    @app.exception_handler(DraftStateError)
    async def _bad_draft_state(request: Request, exc: DraftStateError) -> JSONResponse:
        # 409: the request is well formed, but the draft is not in a state that
        # allows it - picking out of turn, or advancing a finished draft.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body(request, "invalid_draft_state", str(exc)),
        )

    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_body(request, "domain_error", str(exc)),
        )

    @app.exception_handler(CsvImportError)
    async def _csv(request: Request, exc: CsvImportError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_body(request, "invalid_csv", str(exc)),
        )

    @app.exception_handler(ProviderRateLimitError)
    async def _rate_limited(request: Request, exc: ProviderRateLimitError) -> JSONResponse:
        headers = {}
        if exc.retry_after_seconds:
            headers["retry-after"] = str(int(exc.retry_after_seconds))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=_body(request, "provider_rate_limited", str(exc)),
            headers=headers,
        )

    @app.exception_handler(ProviderTimeoutError)
    async def _timeout(request: Request, exc: ProviderTimeoutError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content=_body(request, "provider_timeout", str(exc)),
        )

    @app.exception_handler(ProviderDataError)
    async def _provider_data(request: Request, exc: ProviderDataError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_body(request, "provider_contract_violation", str(exc)),
        )

    @app.exception_handler(ProviderError)
    async def _provider(request: Request, exc: ProviderError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_body(request, "provider_unavailable", str(exc)),
        )
