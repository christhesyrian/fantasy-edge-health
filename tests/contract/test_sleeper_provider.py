"""Sleeper provider contract tests.

These run entirely against saved fixtures - the default suite never touches the
network, so it cannot fail because a provider is having a bad afternoon. The
fixtures reproduce shapes captured from live responses; ``data/fixtures/sleeper``
records when they were last verified.

Live tests against the real API live in ``tests/integration`` behind the ``live``
marker and are deselected by default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from fhe.config import Settings
from fhe.data.providers.base import (
    ProviderDataError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RetryPolicy,
)
from fhe.data.providers.sleeper import SleeperProvider

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parents[2] / "data" / "fixtures" / "sleeper"
BASE = "https://api.sleeper.app/v1"


def fixture(name: str) -> Any:
    """Load a saved provider response."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointed at a temporary data directory."""
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        sleeper_base_url=BASE,
        sleeper_max_rpm=60000,  # do not throttle the test suite
    )


@pytest.fixture
def provider(settings: Settings) -> SleeperProvider:
    """A provider with a fast retry policy so failure tests stay quick."""
    return SleeperProvider(
        settings,
        retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.001, max_delay_seconds=0.002),
    )


class TestUserAndLeagues:
    @respx.mock
    async def test_get_user(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/user/demo_manager").mock(
            return_value=httpx.Response(200, json=fixture("user"))
        )
        user = await provider.get_user("demo_manager")

        assert user is not None
        assert user.user_id == "100000000000000001"
        assert user.username == "demo_manager"

    @respx.mock
    async def test_unknown_user_returns_none(self, provider: SleeperProvider) -> None:
        """Verified live: an unknown username is HTTP 200 with a body of `null`."""
        respx.get(f"{BASE}/user/nobody").mock(return_value=httpx.Response(200, content=b"null"))
        assert await provider.get_user("nobody") is None

    @respx.mock
    async def test_unknown_league_returns_none(self, provider: SleeperProvider) -> None:
        """Verified live: an unknown league is HTTP 404, unlike an unknown user."""
        respx.get(f"{BASE}/league/000").mock(return_value=httpx.Response(404, content=b"null"))
        assert await provider.get_league("000") is None

    @respx.mock
    async def test_unknown_draft_returns_none(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/draft/000").mock(return_value=httpx.Response(404, content=b"null"))
        assert await provider.get_draft("000") is None

    @respx.mock
    async def test_get_leagues(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/user/100000000000000001/leagues/nfl/2026").mock(
            return_value=httpx.Response(200, json=fixture("leagues"))
        )
        leagues = await provider.get_leagues("100000000000000001", "2026")

        assert len(leagues) == 1
        league = leagues[0]
        assert league.league_id == "200000000000000001"
        assert league.total_rosters == 12
        assert league.roster_positions[:3] == ("QB", "RB", "RB")
        assert league.scoring_settings["rec"] == 1.0

    @respx.mock
    async def test_get_rosters(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/league/200000000000000001/rosters").mock(
            return_value=httpx.Response(200, json=fixture("rosters"))
        )
        rosters = await provider.get_rosters("200000000000000001")

        assert len(rosters) == 12
        assert {r.roster_id for r in rosters} == set(range(1, 13))

    @respx.mock
    async def test_get_league_users(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/league/200000000000000001/users").mock(
            return_value=httpx.Response(200, json=fixture("league_users"))
        )
        users = await provider.get_league_users("200000000000000001")

        assert len(users) == 12
        assert users[0].team_name == "Team 1"


class TestDrafts:
    @respx.mock
    async def test_get_draft(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/draft/300000000000000001").mock(
            return_value=httpx.Response(200, json=fixture("draft"))
        )
        draft = await provider.get_draft("300000000000000001")

        assert draft is not None
        assert draft.draft_type == "snake"
        assert draft.status == "drafting"
        assert draft.team_count == 12
        assert draft.rounds == 15
        assert draft.scoring_type == "ppr"
        assert draft.slot_to_roster_id["1"] == 1

    @respx.mock
    async def test_get_draft_picks(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/draft/300000000000000001/picks").mock(
            return_value=httpx.Response(200, json=fixture("picks"))
        )
        picks = await provider.get_draft_picks("300000000000000001")

        assert len(picks) == 12
        first = picks[0]
        assert first.pick_no == 1
        assert first.draft_slot == 1
        assert first.roster_id == 1
        assert first.player_id == "4001"
        assert first.is_keeper is False  # null in the payload

    @respx.mock
    async def test_integer_and_string_roster_ids_both_parse(
        self, provider: SleeperProvider
    ) -> None:
        """The docs say string, the live API sends an integer. Accept both."""
        respx.get(f"{BASE}/draft/a/picks").mock(
            return_value=httpx.Response(200, json=fixture("picks"))
        )
        respx.get(f"{BASE}/draft/b/picks").mock(
            return_value=httpx.Response(200, json=fixture("picks_string_roster_id"))
        )
        integer_form = await provider.get_draft_picks("a")
        string_form = await provider.get_draft_picks("b")

        assert [p.roster_id for p in integer_form] == [p.roster_id for p in string_form]
        assert all(isinstance(p.roster_id, int) for p in string_form)

    @respx.mock
    async def test_undocumented_fields_are_ignored_not_fatal(
        self, provider: SleeperProvider
    ) -> None:
        """Live picks carry a 'reactions' field the documentation never mentions."""
        payload = fixture("picks")
        assert "reactions" in payload[0]
        respx.get(f"{BASE}/draft/x/picks").mock(return_value=httpx.Response(200, json=payload))
        assert len(await provider.get_draft_picks("x")) == 12

    @respx.mock
    async def test_empty_draft_returns_no_picks(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/draft/empty/picks").mock(return_value=httpx.Response(200, json=[]))
        assert await provider.get_draft_picks("empty") == ()

    @respx.mock
    async def test_picks_without_a_player_are_skipped(self, provider: SleeperProvider) -> None:
        """An unfilled auction/keeper slot has a null player_id."""
        payload = [
            *fixture("picks"),
            {"pick_no": 13, "player_id": None, "round": 2, "draft_slot": 1, "draft_id": "x"},
        ]
        respx.get(f"{BASE}/draft/x/picks").mock(return_value=httpx.Response(200, json=payload))
        assert len(await provider.get_draft_picks("x")) == 12


class TestNflStateAndTrending:
    @respx.mock
    async def test_get_nfl_state(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/state/nfl").mock(
            return_value=httpx.Response(200, json=fixture("nfl_state"))
        )
        state = await provider.get_nfl_state()

        assert state is not None
        assert state.season == "2026"
        assert state.season_type == "pre"
        assert state.week == 2

    @respx.mock
    async def test_trending_players(self, provider: SleeperProvider) -> None:
        route = respx.get(url__startswith=f"{BASE}/players/nfl/trending/add").mock(
            return_value=httpx.Response(200, json=fixture("trending_add"))
        )
        trending = await provider.get_trending_players("add", lookback_hours=48, limit=25)

        assert len(trending) == 25
        assert trending[0].count > trending[-1].count
        assert "lookback_hours=48" in str(route.calls[0].request.url)


class TestResilience:
    """Failure modes that will actually happen on draft night."""

    @respx.mock
    async def test_retries_a_transient_server_error(self, provider: SleeperProvider) -> None:
        route = respx.get(f"{BASE}/state/nfl").mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json=fixture("nfl_state")),
            ]
        )
        state = await provider.get_nfl_state()

        assert state is not None
        assert route.call_count == 2

    @respx.mock
    async def test_retries_a_rate_limit(self, provider: SleeperProvider) -> None:
        route = respx.get(f"{BASE}/state/nfl").mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "1"}),
                httpx.Response(200, json=fixture("nfl_state")),
            ]
        )
        assert await provider.get_nfl_state() is not None
        assert route.call_count == 2

    @respx.mock
    async def test_gives_up_after_the_attempt_budget(self, provider: SleeperProvider) -> None:
        route = respx.get(f"{BASE}/state/nfl").mock(return_value=httpx.Response(503))
        with pytest.raises(ProviderUnavailableError):
            await provider.get_nfl_state()
        assert route.call_count == 3

    @respx.mock
    async def test_persistent_rate_limit_raises_rate_limit_error(
        self, provider: SleeperProvider
    ) -> None:
        respx.get(f"{BASE}/state/nfl").mock(return_value=httpx.Response(429))
        with pytest.raises(ProviderRateLimitError):
            await provider.get_nfl_state()

    @respx.mock
    async def test_does_not_retry_a_client_error(self, provider: SleeperProvider) -> None:
        """404 is an answer, not a wobble. Retrying wastes the budget."""
        route = respx.get(f"{BASE}/draft/gone/picks").mock(return_value=httpx.Response(404))
        with pytest.raises(ProviderUnavailableError):
            await provider.get_draft_picks("gone")
        assert route.call_count == 1

    @respx.mock
    async def test_a_vanished_draft_never_looks_like_an_empty_board(
        self, provider: SleeperProvider
    ) -> None:
        """The distinction that protects a live draft from being wiped."""
        respx.get(f"{BASE}/draft/gone/picks").mock(return_value=httpx.Response(404))
        with pytest.raises(ProviderUnavailableError):
            await provider.get_draft_picks("gone")

        respx.get(f"{BASE}/draft/fresh/picks").mock(return_value=httpx.Response(200, json=[]))
        assert await provider.get_draft_picks("fresh") == ()

    @respx.mock
    async def test_timeout_is_classified_and_retried(self, provider: SleeperProvider) -> None:
        route = respx.get(f"{BASE}/state/nfl").mock(side_effect=httpx.ReadTimeout("too slow"))
        with pytest.raises(ProviderTimeoutError):
            await provider.get_nfl_state()
        assert route.call_count == 3

    @respx.mock
    async def test_invalid_json_raises_a_data_error(self, provider: SleeperProvider) -> None:
        respx.get(f"{BASE}/state/nfl").mock(
            return_value=httpx.Response(200, content=b"<html>not json</html>")
        )
        with pytest.raises(ProviderDataError):
            await provider.get_nfl_state()

    @respx.mock
    async def test_malformed_pick_raises_rather_than_coercing(
        self, provider: SleeperProvider
    ) -> None:
        """A corrupt response must never be allowed to overwrite good state."""
        respx.get(f"{BASE}/draft/bad/picks").mock(
            return_value=httpx.Response(200, json=fixture("picks_malformed"))
        )
        with pytest.raises(ProviderDataError, match="pick_no"):
            await provider.get_draft_picks("bad")


class TestPlayerCache:
    @respx.mock
    async def test_players_are_cached_and_not_refetched(
        self, provider: SleeperProvider, tmp_path: Path
    ) -> None:
        """The provider documents once-per-day; the cache is how that is honoured."""
        payload = {"4001": {"player_id": "4001", "position": "RB", "full_name": "First Last"}}
        route = respx.get(f"{BASE}/players/nfl").mock(
            return_value=httpx.Response(200, json=payload)
        )
        cache = tmp_path / "players.json"

        first = await provider.get_all_players(cache_path=cache)
        second = await provider.get_all_players(cache_path=cache)

        assert first == second == payload
        assert route.call_count == 1, "player payload was fetched twice"
        assert cache.exists()

    @respx.mock
    async def test_force_refresh_bypasses_the_cache(
        self, provider: SleeperProvider, tmp_path: Path
    ) -> None:
        payload = {"4001": {"player_id": "4001"}}
        route = respx.get(f"{BASE}/players/nfl").mock(
            return_value=httpx.Response(200, json=payload)
        )
        cache = tmp_path / "players.json"

        await provider.get_all_players(cache_path=cache)
        await provider.get_all_players(cache_path=cache, force_refresh=True)

        assert route.call_count == 2

    @respx.mock
    async def test_corrupt_cache_falls_back_to_a_fetch(
        self, provider: SleeperProvider, tmp_path: Path
    ) -> None:
        cache = tmp_path / "players.json"
        cache.write_text("{ truncated")
        respx.get(f"{BASE}/players/nfl").mock(
            return_value=httpx.Response(200, json={"4001": {"player_id": "4001"}})
        )
        assert await provider.get_all_players(cache_path=cache) == {"4001": {"player_id": "4001"}}


class TestEdgeCacheBypass:
    """Reading past Cloudflare on the two endpoints that change during a draft.

    Sleeper serves picks with `cache-control: public, s-maxage=86400`. Measured
    on 2026-09-05, plain requests returned `cf-cache-status: HIT` with `age:
    6460` - picks nearly two hours stale, identical however fast they were
    repeated. Polling faster cannot beat an edge cache, so a live board is
    impossible without this.
    """

    @respx.mock
    async def test_draft_picks_are_requested_past_the_cache(
        self, provider: SleeperProvider
    ) -> None:
        route = respx.get(url__startswith=f"{BASE}/draft/d1/picks").mock(
            return_value=httpx.Response(200, json=fixture("picks"))
        )

        await provider.get_draft_picks("d1")

        assert route.call_count == 1
        assert "_fhe=" in str(route.calls[0].request.url)

    @respx.mock
    async def test_draft_metadata_is_requested_past_the_cache(
        self, provider: SleeperProvider
    ) -> None:
        """A draft's status is how a live poller learns the draft has finished."""
        route = respx.get(url__startswith=f"{BASE}/draft/d1").mock(
            return_value=httpx.Response(200, json=fixture("draft"))
        )

        await provider.get_draft("d1")

        assert "_fhe=" in str(route.calls[0].request.url)

    @respx.mock
    async def test_every_request_is_a_different_url(self, provider: SleeperProvider) -> None:
        """A reused parameter is a cache key, so the second call would be stale."""
        route = respx.get(url__startswith=f"{BASE}/draft/d1/picks").mock(
            return_value=httpx.Response(200, json=fixture("picks"))
        )

        for _ in range(4):
            await provider.get_draft_picks("d1")

        urls = {str(call.request.url) for call in route.calls}
        assert len(urls) == 4

    @respx.mock
    async def test_the_player_universe_is_not_cache_busted(
        self, provider: SleeperProvider, tmp_path: Path
    ) -> None:
        """14.6 MB that changes daily, cached locally for twenty hours on purpose.

        Bypassing the edge here would be pure cost to the provider for nothing,
        which is why the bypass is opt-in per call rather than a client default.
        """
        route = respx.get(f"{BASE}/players/nfl").mock(
            return_value=httpx.Response(200, json={"1": {"player_id": "1"}})
        )

        await provider.get_all_players(cache_path=tmp_path / "players.json")

        assert "_fhe=" not in str(route.calls[0].request.url)
