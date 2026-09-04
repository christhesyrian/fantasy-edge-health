"""The shared-password gate.

The gate exists so a deployed instance is reachable by the people who were told
the password and nobody else. Most of these tests describe a way that could
silently fail to be true.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.api.app import create_app
from fhe.api.services.access import (
    COOKIE_NAME,
    AttemptLimiter,
    issue_token,
    password_matches,
    token_is_valid,
)
from fhe.config import Environment, Settings
from fhe.db import Base

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"


@pytest.fixture
def gated_settings(tmp_path: Path) -> Settings:
    """Settings with the gate switched on."""
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        log_level="WARNING",
        access_password=PASSWORD,
    )


@pytest.fixture
async def gated_app(gated_settings: Settings) -> AsyncIterator[FastAPI]:
    application = create_app(gated_settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield application


@pytest.fixture
async def gated_client(gated_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=gated_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def sign_in(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    """Exchange a password for a session cookie."""
    return await client.post("/api/v1/auth/session", json={"password": password})


class TestGate:
    async def test_the_api_is_closed_without_a_session(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        response = await gated_client.post(
            "/api/v1/simulations",
            json={"team_count": 12, "user_draft_slot": 1, "scoring_format": "ppr", "seed": 1},
        )
        assert response.status_code == 401
        assert response.json()["error"] == "not_authenticated"

    async def test_signing_in_opens_it(self, gated_client: httpx.AsyncClient) -> None:
        assert (await sign_in(gated_client)).status_code == 200

        response = await gated_client.post(
            "/api/v1/simulations",
            json={"team_count": 12, "user_draft_slot": 1, "scoring_format": "ppr", "seed": 1},
        )
        assert response.status_code == 201

    async def test_a_new_route_is_closed_by_default(self, gated_app: FastAPI) -> None:
        """Why this is middleware and not a route dependency.

        A dependency has to be remembered on every new endpoint, and forgetting
        one leaves it quietly public. Anything not on the open list is closed
        without anybody having to think about it.
        """
        transport = httpx.ASGITransport(app=gated_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for path in ("/docs", "/openapi.json", "/api/v1/players", "/api/v1/sleeper/state"):
                assert (await client.get(path)).status_code == 401, path

    async def test_health_stays_open(self, gated_client: httpx.AsyncClient) -> None:
        """A platform probes health before anything could hold a cookie.

        A gated health check reads as a dead service and gets restarted forever.
        """
        assert (await gated_client.get("/api/v1/health")).status_code == 200
        assert (await gated_client.get("/api/v1/health/ready")).status_code in (200, 503)

    async def test_signing_out_closes_it_again(self, gated_client: httpx.AsyncClient) -> None:
        await sign_in(gated_client)
        await gated_client.delete("/api/v1/auth/session")

        assert (await gated_client.get("/api/v1/players")).status_code == 401

    async def test_the_wrong_password_is_refused(self, gated_client: httpx.AsyncClient) -> None:
        response = await sign_in(gated_client, "hunter2")

        assert response.status_code == 401
        assert response.json() == {"required": True, "authenticated": False}
        assert COOKIE_NAME not in gated_client.cookies

    async def test_a_forged_cookie_is_refused(self, gated_client: httpx.AsyncClient) -> None:
        far_future = int(time.time()) + 999_999
        gated_client.cookies.set(COOKIE_NAME, f"{far_future}.{'0' * 64}")

        assert (await gated_client.get("/api/v1/players")).status_code == 401

    async def test_status_says_whether_a_password_is_needed(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        """So the frontend can show the form without provoking a failed request."""
        before = await gated_client.get("/api/v1/auth/session")
        assert before.json() == {"required": True, "authenticated": False}

        await sign_in(gated_client)
        after = await gated_client.get("/api/v1/auth/session")
        assert after.json() == {"required": True, "authenticated": True}


class TestOpenInstance:
    async def test_no_password_means_no_gate(self, client: httpx.AsyncClient) -> None:
        """Local development must not need a login."""
        assert (await client.get("/api/v1/players")).status_code == 200

    async def test_status_reports_that_none_is_required(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/v1/auth/session")).json() == {
            "required": False,
            "authenticated": True,
        }

    async def test_an_open_instance_says_so_in_its_degradations(
        self, client: httpx.AsyncClient
    ) -> None:
        body = (await client.get("/api/v1/health/ready")).json()
        assert any("FHE_ACCESS_PASSWORD" in d for d in body["degradations"])

    def test_production_without_a_password_refuses_to_start(self, tmp_path: Path) -> None:
        """The failure this prevents is silent: everything works.

        An open production instance behaves perfectly while serving every draft
        and import to anyone with the URL, so refusing to start is the only
        signal that cannot be missed.
        """
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path,
            env=Environment.PRODUCTION,
            database_url="postgresql+asyncpg://u:p@localhost/db",
        )
        with pytest.raises(RuntimeError, match="FHE_ACCESS_PASSWORD"):
            create_app(settings)


class TestCookie:
    async def test_the_cookie_cannot_be_read_by_a_script(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        """HttpOnly, so an injected script cannot steal the session."""
        header = (await sign_in(gated_client)).headers["set-cookie"].lower()
        assert "httponly" in header

    async def test_locally_the_cookie_is_not_marked_secure(
        self, gated_client: httpx.AsyncClient
    ) -> None:
        """Secure over plain HTTP would stop the browser storing it at all."""
        header = (await sign_in(gated_client)).headers["set-cookie"].lower()
        assert "secure" not in header
        assert "samesite=lax" in header

    async def test_in_production_it_crosses_sites_securely(self, tmp_path: Path) -> None:
        """A deployed frontend and API sit on different sites.

        A browser sends a cookie across sites only for SameSite=None, and
        accepts SameSite=None only when Secure.
        """
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path,
            log_level="WARNING",
            env=Environment.PRODUCTION,
            database_url="postgresql+asyncpg://u:p@localhost/db",
            access_password=PASSWORD,
        )
        application = create_app(settings)
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            header = (await sign_in(client)).headers["set-cookie"].lower()

        assert "secure" in header
        assert "samesite=none" in header


class TestToken:
    def test_a_token_survives_a_restart(self, gated_settings: Settings) -> None:
        """Signing is stateless, so nobody is logged out mid-draft by a deploy."""
        token = issue_token(gated_settings)
        assert token_is_valid(gated_settings, token)

    def test_a_token_expires(self, gated_settings: Settings) -> None:
        token = issue_token(gated_settings, now=0.0)
        expired_at = gated_settings.access_session_hours * 3600 + 1
        assert not token_is_valid(gated_settings, token, now=expired_at)

    def test_changing_the_password_invalidates_every_session(
        self, gated_settings: Settings, tmp_path: Path
    ) -> None:
        """The one operation you want after a password reaches a group chat.

        The key that signed the old tokens is derived from the password, so it
        is gone, and no session store has to be cleared for that to be true.
        """
        token = issue_token(gated_settings)
        rotated = Settings(_env_file=None, data_dir=tmp_path, access_password="something else")

        assert not token_is_valid(rotated, token)

    def test_the_token_does_not_contain_the_password(self, gated_settings: Settings) -> None:
        assert PASSWORD not in issue_token(gated_settings)

    @pytest.mark.parametrize(
        "token",
        ["", "nonsense", "1.2.3", ".", "9999999999.", ".abc", "x" * 300],
    )
    def test_malformed_tokens_are_refused_without_raising(
        self, gated_settings: Settings, token: str
    ) -> None:
        assert not token_is_valid(gated_settings, token)

    def test_an_unsigned_but_well_formed_token_is_refused(self, gated_settings: Settings) -> None:
        assert not token_is_valid(gated_settings, f"{int(time.time()) + 3600}.deadbeef")

    def test_password_comparison_accepts_only_the_exact_password(
        self, gated_settings: Settings
    ) -> None:
        assert password_matches(gated_settings, PASSWORD)
        assert not password_matches(gated_settings, PASSWORD + " ")
        assert not password_matches(gated_settings, PASSWORD[:-1])


class TestAttemptLimiter:
    def test_guessing_is_allowed_up_to_the_limit(self) -> None:
        limiter = AttemptLimiter(max_attempts=3, lockout_seconds=60)
        for _ in range(2):
            limiter.record_failure("1.2.3.4", now=0.0)

        assert not limiter.is_locked("1.2.3.4", now=0.0)

    def test_and_refused_past_it(self) -> None:
        """A shared password is guessable by anyone patient.

        This is what makes patience expensive.
        """
        limiter = AttemptLimiter(max_attempts=3, lockout_seconds=60)
        for _ in range(3):
            limiter.record_failure("1.2.3.4", now=0.0)

        assert limiter.is_locked("1.2.3.4", now=0.0)
        assert limiter.retry_after_seconds("1.2.3.4", now=0.0) == 60

    def test_the_lockout_lifts(self) -> None:
        limiter = AttemptLimiter(max_attempts=1, lockout_seconds=60)
        limiter.record_failure("1.2.3.4", now=0.0)

        assert not limiter.is_locked("1.2.3.4", now=61.0)

    def test_getting_it_right_forgives_earlier_mistakes(self) -> None:
        """Mistyping twice must not lock somebody out of their own draft."""
        limiter = AttemptLimiter(max_attempts=3, lockout_seconds=60)
        limiter.record_failure("1.2.3.4", now=0.0)
        limiter.record_failure("1.2.3.4", now=1.0)
        limiter.clear("1.2.3.4")
        limiter.record_failure("1.2.3.4", now=2.0)

        assert not limiter.is_locked("1.2.3.4", now=2.0)

    def test_one_address_locking_out_does_not_lock_out_another(self) -> None:
        limiter = AttemptLimiter(max_attempts=1, lockout_seconds=60)
        limiter.record_failure("1.2.3.4", now=0.0)

        assert limiter.is_locked("1.2.3.4", now=0.0)
        assert not limiter.is_locked("5.6.7.8", now=0.0)

    async def test_repeated_wrong_guesses_are_refused_by_the_endpoint(
        self, gated_client: httpx.AsyncClient, gated_app: FastAPI
    ) -> None:
        limit = gated_app.state.settings.access_max_attempts
        for _ in range(limit):
            assert (await sign_in(gated_client, "wrong")).status_code == 401

        locked = await sign_in(gated_client, "wrong")
        assert locked.status_code == 429
        assert "retry-after" in locked.headers

        # And the correct password is refused too, or the lockout would be
        # trivially bypassed by the very thing it is guarding.
        assert (await sign_in(gated_client)).status_code == 429
