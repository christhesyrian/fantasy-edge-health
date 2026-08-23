"""Simulation endpoints and the live event stream.

Together these cover the demo acceptance path: start a draft, watch picks
arrive, see drafted players disappear, see the roster and recommendations
update, and understand why a player is recommended.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from tests.api.conftest import LiveServer

pytestmark = pytest.mark.integration


class TestLifecycle:
    async def test_create_returns_a_seeded_draft(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/simulations", json={"team_count": 12, "user_draft_slot": 5, "seed": 7}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["is_demo"] is True
        assert body["seed"] == 7
        assert body["total_picks"] == 12 * 15
        assert body["pick_count"] == 0

    async def test_the_same_seed_reproduces_the_same_draft(self, client: httpx.AsyncClient) -> None:
        """A reproducible demo is also a usable regression test."""

        async def first_picks(seed: int) -> list[str]:
            created = await client.post(
                "/api/v1/simulations", json={"seed": seed, "user_draft_slot": 5}
            )
            sim = created.json()["simulation_id"]
            advanced = await client.post(
                f"/api/v1/simulations/{sim}/advance",
                json={"picks": 4, "stop_at_user_turn": False},
            )
            return [p["player"]["name"] for p in advanced.json()]

        assert await first_picks(99) == await first_picks(99)
        assert await first_picks(99) != await first_picks(100)

    async def test_unknown_simulation_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/simulations/nope")
        assert response.status_code == 404
        assert response.json()["error"] == "session_not_found"
        assert response.json()["request_id"]

    async def test_invalid_league_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/api/v1/simulations", json={"team_count": 1})
        assert response.status_code == 422

    async def test_reset_returns_to_a_pristine_draft(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        await client.post(
            f"/api/v1/simulations/{simulation}/advance",
            json={"picks": 10, "stop_at_user_turn": False},
        )
        response = await client.post(f"/api/v1/simulations/{simulation}/reset")
        assert response.status_code == 200
        assert response.json()["pick_count"] == 0


class TestBoard:
    async def test_board_is_fully_decomposable(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        """Never present an opaque score: the arithmetic ships with the number."""
        board = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        best = board["best_pick"]
        assert best is not None
        assert best["reasons"]

        total = round(sum(c["points"] for c in best["components"]), 2)
        assert total == pytest.approx(best["overall_score"], abs=0.01)

    async def test_board_reports_its_own_provenance_and_demo_status(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        board = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        assert board["is_demo"] is True
        assert board["provenance"]
        assert all(p["source"] == "synthetic-demo" for p in board["provenance"])

    async def test_headline_picks_are_populated(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        board = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        for key in ("best_pick", "safest_pick", "highest_upside", "best_value"):
            assert board[key] is not None, key

    async def test_depth_limits_the_payload(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        board = (await client.get(f"/api/v1/drafts/{simulation}/board?depth=5")).json()
        assert len(board["recommendations"]) == 5

    async def test_kickers_are_not_recommended_in_round_one(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        """Regression, end to end through the API this time."""
        board = (await client.get(f"/api/v1/drafts/{simulation}/board?depth=40")).json()
        top_positions = {r["position"] for r in board["recommendations"][:40]}
        assert "K" not in top_positions
        assert "DEF" not in top_positions

    async def test_league_settings_expose_replacement_ranks(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        board = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        ranks = board["league"]["replacement_ranks"]
        assert ranks["QB"] == 12
        assert ranks["RB"] == 29


class TestDraftFlow:
    async def test_drafted_players_disappear_from_the_board(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        await client.post(
            f"/api/v1/simulations/{simulation}/advance",
            json={"picks": 4, "stop_at_user_turn": False},
        )
        picks = (await client.get(f"/api/v1/drafts/{simulation}/board?depth=500")).json()
        drafted = {p["player"]["player_uuid"] for p in picks["recent_picks"]}
        available = {r["player_uuid"] for r in picks["recommendations"]}
        assert drafted
        assert not (drafted & available)

    async def test_user_pick_updates_the_roster(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        await client.post(
            f"/api/v1/simulations/{simulation}/advance",
            json={"picks": 50, "stop_at_user_turn": True},
        )
        board = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        assert board["is_user_on_the_clock"] is True
        target = board["best_pick"]["player_uuid"]

        response = await client.post(
            f"/api/v1/simulations/{simulation}/pick", json={"player_uuid": target}
        )
        assert response.status_code == 200

        after = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        rostered = {
            slot["player"]["player_uuid"] for slot in after["my_roster"]["lineup"] if slot["player"]
        }
        assert target in rostered
        assert after["is_user_on_the_clock"] is False

    async def test_picking_out_of_turn_is_a_conflict_not_a_validation_error(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        """The request is well formed; the draft state is what forbids it."""
        board = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        await client.post(
            f"/api/v1/simulations/{simulation}/advance",
            json={"picks": 1, "stop_at_user_turn": False},
        )
        response = await client.post(
            f"/api/v1/simulations/{simulation}/pick",
            json={"player_uuid": board["recommendations"][0]["player_uuid"]},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "invalid_draft_state"

    async def test_recommendations_change_as_the_draft_progresses(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        before = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()
        await client.post(
            f"/api/v1/simulations/{simulation}/advance",
            json={"picks": 24, "stop_at_user_turn": False},
        )
        after = (await client.get(f"/api/v1/drafts/{simulation}/board")).json()

        assert [r["player_uuid"] for r in before["recommendations"][:5]] != [
            r["player_uuid"] for r in after["recommendations"][:5]
        ]

    async def test_advance_stops_at_the_user_turn(
        self, client: httpx.AsyncClient, simulation: str
    ) -> None:
        response = await client.post(
            f"/api/v1/simulations/{simulation}/advance",
            json={"picks": 500, "stop_at_user_turn": True},
        )
        assert response.status_code == 200
        assert len(response.json()) == 4  # slot 5 picks fifth
        state = (await client.get(f"/api/v1/simulations/{simulation}")).json()
        assert state["is_user_on_the_clock"] is True


class TestEventStream:
    """Server-sent events, exercised against a real HTTP server.

    httpx's ASGITransport never delivers ``http.disconnect``, so a streaming
    response never completes under it. These run through uvicorn on a real
    socket, which is also the only way to test that the stream shuts down when a
    client goes away.
    """

    async def _read_frames(
        self, server: LiveServer, *, want: int, drive: bool
    ) -> list[dict[str, Any]]:
        """Open the stream, optionally advance the draft, and collect frames."""
        async with httpx.AsyncClient(base_url=server.base_url, timeout=20) as client:
            created = await client.post(
                "/api/v1/simulations", json={"seed": 42, "user_draft_slot": 5}
            )
            simulation_id = created.json()["simulation_id"]

            frames: list[dict[str, Any]] = []
            async with client.stream("GET", f"/api/v1/drafts/{simulation_id}/events") as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")

                task: asyncio.Task[None] | None = None
                if drive:

                    async def advance() -> None:
                        await asyncio.sleep(0.2)
                        await client.post(
                            f"/api/v1/simulations/{simulation_id}/advance",
                            json={"picks": 3, "stop_at_user_turn": False},
                        )

                    task = asyncio.create_task(advance())

                try:
                    async with asyncio.timeout(20):
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            frames.append(json.loads(line.removeprefix("data:").strip()))
                            if len(frames) >= want:
                                break
                finally:
                    if task is not None:
                        await task
            return frames

    async def test_stream_opens_with_a_connection_status_event(
        self, live_server: LiveServer
    ) -> None:
        frames = await self._read_frames(live_server, want=1, drive=False)

        assert frames
        assert frames[0]["type"] == "connection_status"
        assert frames[0]["payload"]["status"] == "LIVE"
        assert frames[0]["payload"]["is_demo"] is True

    async def test_picks_are_broadcast_with_increasing_sequence(
        self, live_server: LiveServer
    ) -> None:
        """Sequence numbers are how a reconnecting client detects a gap."""
        frames = await self._read_frames(live_server, want=5, drive=True)

        picks = [f for f in frames if f["type"] == "pick_made"]
        assert len(picks) >= 3, [f["type"] for f in frames]

        sequences = [int(f["sequence"]) for f in picks]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences), "sequence numbers repeated"
        assert picks[0]["payload"]["player_name"]

    async def test_board_update_follows_the_picks(self, live_server: LiveServer) -> None:
        frames = await self._read_frames(live_server, want=5, drive=True)
        assert any(f["type"] == "board_updated" for f in frames)

    async def test_no_event_is_lost_between_subscribing_and_reading(
        self, live_server: LiveServer
    ) -> None:
        """Regression: a lazily-registering subscriber missed everything
        published between the handler starting and its first loop tick."""
        frames = await self._read_frames(live_server, want=4, drive=True)
        pick_sequences = [int(f["sequence"]) for f in frames if f["type"] == "pick_made"]
        assert pick_sequences[:3] == [1, 2, 3], f"gap in delivery: {pick_sequences}"
