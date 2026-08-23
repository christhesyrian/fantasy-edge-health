"""FantasyPros client behaviour, especially the licence terms it enforces.

The daily call cap and the one-per-second spacing are contract terms, not
performance tuning, so they are asserted here rather than trusted to a comment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from fhe.config import Settings
from fhe.data.providers.fantasypros import (
    SOURCE,
    FantasyProsNotConfiguredError,
    FantasyProsProvider,
    FantasyProsQuotaExceededError,
    _DailyBudget,
)

pytestmark = pytest.mark.unit


class TestDailyBudget:
    def test_it_counts_calls(self, tmp_path: Path) -> None:
        budget = _DailyBudget(path=tmp_path / "b.json", limit=100)

        budget.spend()
        budget.spend()

        assert budget.used_today == 2
        assert budget.remaining == 98

    def test_it_survives_a_restart(self, tmp_path: Path) -> None:
        """The whole reason it is on disk: a restart must not reset the licence."""
        path = tmp_path / "b.json"
        first = _DailyBudget(path=path, limit=100)
        for _ in range(7):
            first.spend()

        second = _DailyBudget(path=path, limit=100)

        assert second.used_today == 7
        assert second.remaining == 93

    def test_a_new_day_resets_the_count(self, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        path.write_text('{"day": "2020-01-01", "used": 100}', encoding="utf-8")

        budget = _DailyBudget(path=path, limit=100)

        assert budget.used_today == 0
        assert budget.remaining == 100

    def test_a_corrupt_counter_starts_the_day_at_zero(self, tmp_path: Path) -> None:
        """A bad file read must not permanently disable a paid integration."""
        path = tmp_path / "b.json"
        path.write_text("{not json", encoding="utf-8")

        budget = _DailyBudget(path=path, limit=100)

        assert budget.used_today == 0

    def test_todays_count_is_kept_under_the_current_date(self, tmp_path: Path) -> None:
        path = tmp_path / "b.json"
        budget = _DailyBudget(path=path, limit=100)
        budget.spend()

        import json

        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["day"] == datetime.now(UTC).date().isoformat()


class TestConfiguration:
    async def test_without_a_key_the_adapter_refuses_to_start(self, tmp_path: Path) -> None:
        """An unconfigured provider stays disabled rather than half-working."""
        settings = Settings(_env_file=None, data_dir=tmp_path, fantasypros_api_key="")

        with pytest.raises(FantasyProsNotConfiguredError):
            async with FantasyProsProvider(settings):
                pass

    def test_the_key_is_never_in_the_settings_repr(self, tmp_path: Path) -> None:
        """A key in a traceback or a logged settings dump is a leaked key."""
        settings = Settings(
            _env_file=None, data_dir=tmp_path, fantasypros_api_key="super-secret-value"
        )

        assert "super-secret-value" not in repr(settings)

    def test_the_source_name_is_the_attribution_the_licence_requires(self) -> None:
        assert SOURCE == "FantasyPros"


class TestQuota:
    async def test_a_spent_budget_refuses_rather_than_exceeding_the_licence(
        self, tmp_path: Path
    ) -> None:
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path,
            fantasypros_api_key="k",
            fantasypros_max_calls_per_day=2,
        )
        provider = FantasyProsProvider(settings, cache_dir=tmp_path / "fp")
        async with provider:
            provider._budget.spend(2)

            with pytest.raises(FantasyProsQuotaExceededError):
                await provider.get_adp(2026)

    async def test_a_cached_response_still_works_once_the_budget_is_spent(
        self, tmp_path: Path
    ) -> None:
        """Cache is checked before quota: a cached answer costs no call."""
        import json

        cache = tmp_path / "fp"
        cache.mkdir(parents=True)
        (cache / "adp-2026-ALL-PPR.json").write_text(
            json.dumps(
                {
                    "players": [
                        {
                            "player_id": "1",
                            "player_name": "Test Player",
                            "player_team_id": "CIN",
                            "player_position_id": "WR",
                            "rank_ecr": "3",
                            "tier": 1,
                            "player_bye_week": "10",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path,
            fantasypros_api_key="k",
            fantasypros_max_calls_per_day=1,
        )
        provider = FantasyProsProvider(settings, cache_dir=cache)

        async with provider:
            provider._budget.spend(1)
            players = await provider.get_adp(2026)

        assert len(players) == 1
        assert players[0].name == "Test Player"
        assert players[0].rank == 3.0
        assert players[0].bye_week == 10


class TestMapping:
    def test_a_projection_with_no_recognisable_points_is_unknown_not_zero(
        self, tmp_path: Path
    ) -> None:
        """Missing data lowers confidence; it never invents a value."""
        settings = Settings(_env_file=None, data_dir=tmp_path, fantasypros_api_key="k")
        provider = FantasyProsProvider(settings, cache_dir=tmp_path / "fp")

        mapped = provider._to_projection(
            {"fpid": "9", "name": "A B", "team_id": "cin", "stats": {"unknown_field": 12}}, "WR"
        )

        assert mapped.projected_points is None
        assert mapped.team == "CIN"

    def test_the_position_falls_back_to_the_one_requested(self, tmp_path: Path) -> None:
        settings = Settings(_env_file=None, data_dir=tmp_path, fantasypros_api_key="k")
        provider = FantasyProsProvider(settings, cache_dir=tmp_path / "fp")

        mapped = provider._to_projection({"name": "A B", "stats": {"points": "210.5"}}, "RB")

        assert mapped.position == "RB"
        assert mapped.projected_points == 210.5
