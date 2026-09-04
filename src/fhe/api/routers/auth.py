"""Signing in to a password-protected instance.

Three endpoints and no user model: exchange the shared password for a session
cookie, ask whether the current cookie is good, and throw it away.

Why a cookie rather than a bearer token
---------------------------------------
The war room's live feed is an ``EventSource``, and the browser API for it
cannot set request headers. A cookie is the only credential the browser will
attach to a stream, so choosing anything else would mean leaving the live feed
either unauthenticated or reauthenticated through a query parameter - and a
credential in a URL ends up in access logs, proxy logs, and browser history.

The cookie is ``HttpOnly``, so a script on the page cannot read it even if one
gets in, and ``Secure`` with ``SameSite=None`` in production because the
frontend and the API are deployed to different sites.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import Field

from fhe.api.deps import SettingsDep
from fhe.api.schemas import ApiModel
from fhe.api.services.access import (
    COOKIE_NAME,
    AttemptLimiter,
    issue_token,
    password_matches,
    token_is_valid,
)
from fhe.config import Settings
from fhe.observability import get_logger

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)


class SignInRequest(ApiModel):
    """The shared password."""

    password: str = Field(min_length=1, max_length=256, repr=False)


class SessionStatus(ApiModel):
    """Whether this browser may use the instance, and whether it needs to ask.

    ``required`` is what lets the frontend decide between showing a password
    form and going straight to the board, without provoking a failed request to
    find out.
    """

    required: bool = Field(description="Whether this instance is password protected.")
    authenticated: bool


def _client_address(request: Request) -> str:
    """Best available identifier for the caller, for rate limiting.

    Behind a proxy the socket address is the proxy's, so the first hop in
    ``x-forwarded-for`` is used when present. It is spoofable, which matters
    less than it sounds: forging it spreads an attacker's attempts across
    buckets but does not raise the total allowance, and the alternative is
    every user behind one proxy sharing a single bucket.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    """Attach the session cookie with the strictest attributes that still work."""
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(settings.access_session_hours * 3600),
        httponly=True,
        # A deployed frontend and API sit on different sites, and a browser
        # sends a cookie across sites only for SameSite=None, which browsers in
        # turn accept only when Secure. Locally both are on localhost, where
        # Lax works and Secure would stop the cookie being stored at all over
        # plain HTTP.
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
        path="/",
    )


@router.get("/session", response_model=SessionStatus, summary="Session status")
async def session_status(request: Request, settings: SettingsDep) -> SessionStatus:
    """Report whether a password is needed and whether this browser has passed."""
    if not settings.access_enabled:
        return SessionStatus(required=False, authenticated=True)
    return SessionStatus(
        required=True,
        authenticated=token_is_valid(settings, request.cookies.get(COOKIE_NAME)),
    )


@router.post(
    "/session",
    response_model=SessionStatus,
    summary="Sign in",
    responses={
        401: {"description": "Wrong password."},
        429: {"description": "Too many attempts from this address."},
    },
)
async def sign_in(
    body: SignInRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
) -> SessionStatus:
    """Exchange the shared password for a session cookie."""
    if not settings.access_enabled:
        # Nothing to sign in to. Reported as success so a frontend pointed at an
        # open instance is not stuck on a form it can never satisfy.
        return SessionStatus(required=False, authenticated=True)

    address = _client_address(request)
    limiter: AttemptLimiter = request.app.state.attempt_limiter

    if limiter.is_locked(address):
        retry_after = limiter.retry_after_seconds(address)
        log.warning("sign_in_locked_out", retry_after_seconds=retry_after)
        response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
        response.headers["retry-after"] = str(retry_after)
        return SessionStatus(required=True, authenticated=False)

    if not password_matches(settings, body.password):
        limiter.record_failure(address)
        # The address is not logged: it identifies a person, and knowing that
        # *someone* is guessing is the operationally useful part.
        log.warning("sign_in_failed")
        response.status_code = status.HTTP_401_UNAUTHORIZED
        return SessionStatus(required=True, authenticated=False)

    limiter.clear(address)
    _set_cookie(response, settings, issue_token(settings))
    log.info("sign_in_succeeded")
    return SessionStatus(required=True, authenticated=True)


@router.delete("/session", response_model=SessionStatus, summary="Sign out")
async def sign_out(response: Response, settings: SettingsDep) -> SessionStatus:
    """Discard the session cookie."""
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=settings.is_production,
        samesite="none" if settings.is_production else "lax",
    )
    return SessionStatus(required=settings.access_enabled, authenticated=False)
