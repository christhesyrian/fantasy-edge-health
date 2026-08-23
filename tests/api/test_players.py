"""Draft-independent player reads.

These back the two screens that are not about a draft — a rankings table and a
health centre — so the assertions are about browsing and filtering, not about
recommendation, which stays in the engine.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import FantasyProjection
from fhe.db.models.player import Player
from tests.integration.conftest import NOW

pytestmark = pytest.mark.integration

POSITIONS = ["RB", "WR", "QB", "TE"]


async def _seed(app: FastAPI, count: int = 12) -> None:
    """A handful of players across positions."""
    async with app.state.session_factory() as session:
        for index in range(count):
            uuid = f"p-{index:03d}"
            session.add(
                Player(
                    player_uuid=uuid,
                    full_name=f"Player {index}",
                    normalized_name=f"player{index}",
                    position=POSITIONS[index % len(POSITIONS)],
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
        await session.commit()


async def test_browsing_players_returns_a_page(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _seed(app)

    response = await client.get("/api/v1/players?season=2026&limit=5")

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 5
    assert len(body["players"]) == 5
    assert body["total"] >= 5
    # The same contract the war-room drawer renders.
    assert "health" in body["players"][0]


async def test_filtering_by_position(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _seed(app)

    response = await client.get("/api/v1/players?season=2026&position=QB")

    assert response.status_code == 200
    positions = {player["position"] for player in response.json()["players"]}
    assert positions == {"QB"}


async def test_searching_by_name(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _seed(app)

    response = await client.get("/api/v1/players?season=2026&query=player 3")

    assert response.status_code == 200
    names = [player["name"] for player in response.json()["players"]]
    assert names and all("Player 3" in name for name in names)


async def test_paging_does_not_repeat_players(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _seed(app)

    first = await client.get("/api/v1/players?season=2026&limit=4&offset=0")
    second = await client.get("/api/v1/players?season=2026&limit=4&offset=4")

    ids = [p["player_uuid"] for p in first.json()["players"]]
    more = [p["player_uuid"] for p in second.json()["players"]]
    assert set(ids).isdisjoint(more)


async def test_player_detail_outside_a_draft(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _seed(app)

    response = await client.get("/api/v1/players/p-000?season=2026")

    assert response.status_code == 200
    body = response.json()
    assert body["player_uuid"] == "p-000"
    # Health must carry its limitations wherever it is shown.
    assert body["health"]["limitations"]


async def test_an_unknown_player_is_a_404(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _seed(app)

    response = await client.get("/api/v1/players/nobody?season=2026")

    assert response.status_code == 404


async def test_an_empty_database_is_an_empty_page_not_an_error(
    client: httpx.AsyncClient,
) -> None:
    """Nothing ingested yet is a normal state, and says so through warnings."""
    response = await client.get("/api/v1/players?season=2026")

    assert response.status_code == 200
    assert response.json()["players"] == []
