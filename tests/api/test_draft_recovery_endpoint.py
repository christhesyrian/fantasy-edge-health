"""The war room comes back after an API restart, through the HTTP surface.

The service-level behaviour is covered in
``tests/integration/test_session_recovery.py``. This asserts the part the user
actually experiences: reloading the war room during a draft returns a board
rather than a 404, without anyone reconnecting the league by hand.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import fhe.api.routers.drafts as drafts_router
from fhe.api.app import create_app
from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.api.services.league_connect import connect_sleeper_draft
from fhe.config import Settings
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.player import Player, PlayerExternalId
from tests.integration.test_league_connect import (
    DRAFT_ID,
    LEAGUE_ID,
    NOW,
    USER_ID,
    FakeSleeper,
    make_draft,
    make_league,
    sleeper_pick,
)

pytestmark = pytest.mark.integration


async def _seed_players(app: FastAPI) -> None:
    """A small canonical pool, so the connect path has something to rank."""
    factory = app.state.session_factory
    async with factory() as session:
        for index in range(24):
            uuid = f"p-{index:03d}"
            session.add(
                Player(
                    player_uuid=uuid,
                    full_name=f"Player {index}",
                    normalized_name=f"player{index}",
                    position="RB",
                    team="SEA",
                    age=26.0,
                    years_experience=4,
                    is_active=True,
                    popularity_rank=index + 1,
                    identity_method="DIRECT_GSIS",
                    identity_confidence=1.0,
                    source="test",
                )
            )
            session.add(
                PlayerExternalId(player_uuid=uuid, system="sleeper_id", external_id=f"s-{index}")
            )
            session.add(
                FantasyProjection(
                    player_uuid=uuid,
                    season=2026,
                    week=SEASON_LONG_WEEK,
                    scoring_format="ppr",
                    projected_points=300.0 - index * 5,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
            session.add(
                AdpSnapshot(
                    player_uuid=uuid,
                    season=2026,
                    scoring_format="ppr",
                    adp=float(index + 1),
                    snapshot_date=NOW,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
        await session.commit()


class _StubProvider:
    """Stands in for SleeperProvider, including its async-context lifetime."""

    def __init__(self, picks: tuple[Any, ...]) -> None:
        self._inner = FakeSleeper(make_league(), make_draft(), picks)

    async def __aenter__(self) -> _StubProvider:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_league(self, league_id: str) -> Any:
        return await self._inner.get_league(league_id)

    async def get_draft(self, draft_id: str) -> Any:
        return await self._inner.get_draft(draft_id)

    async def get_draft_picks(self, draft_id: str) -> Any:
        return await self._inner.get_draft_picks(draft_id)


async def test_the_board_survives_an_api_restart(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_players(app)
    picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))

    await connect_sleeper_draft(
        app.state.session_factory,
        FakeSleeper(make_league(), make_draft(), picks),
        app.state.registry,
        league_id=LEAGUE_ID,
        draft_id=DRAFT_ID,
        user_id=USER_ID,
    )
    before = await client.get(f"/api/v1/drafts/{DRAFT_ID}/board")
    assert before.status_code == 200
    assert before.json()["current_pick"] == 13

    # The restart: in-memory sessions are gone, the database and the provider
    # are not. The router must rebuild rather than 404.
    app.state.registry = DraftSessionRegistry(app.state.registry.event_bus)
    monkeypatch.setattr(drafts_router, "SleeperProvider", lambda _settings: _StubProvider(picks))

    after = await client.get(f"/api/v1/drafts/{DRAFT_ID}/board")

    assert after.status_code == 200
    assert after.json()["current_pick"] == 13
    assert after.json()["my_roster"] is not None


async def test_an_unknown_draft_is_still_a_404(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must not turn every typo into a session."""
    monkeypatch.setattr(drafts_router, "SleeperProvider", lambda _settings: _StubProvider(()))

    response = await client.get("/api/v1/drafts/not-a-real-draft/board")

    assert response.status_code == 404


async def test_an_unknown_draft_is_a_404_even_with_no_schema(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo path has no migrations applied, and must still 404 cleanly.

    Regression: the recovery lookup queried a `drafts` table that does not
    exist on a fresh SQLite file, so every request for an unknown draft
    returned 500 instead of "no such draft". The zero-infrastructure demo is
    exactly this configuration, which made it the default experience.
    """
    monkeypatch.setattr(drafts_router, "SleeperProvider", lambda _settings: _StubProvider(()))
    application = create_app(settings)

    # Note the absence of `Base.metadata.create_all`: no schema, on purpose.
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.get("/api/v1/drafts/anything/board")

    assert response.status_code == 404
    assert response.json()["error"] == "session_not_found"
