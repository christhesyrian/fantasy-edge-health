"""Tests for league configuration and replacement-level maths."""

from __future__ import annotations

import pytest

from fhe.core.errors import LeagueConfigurationError
from fhe.core.league import LeagueSettings
from fhe.core.types import DraftType, Position, RosterSlot, ScoringFormat

pytestmark = pytest.mark.unit


class TestReplacementRank:
    def test_standard_twelve_team_matches_conventional_baselines(
        self, league: LeagueSettings
    ) -> None:
        """QB12/RB29/WR29/TE14 are the accepted baselines for this shape."""
        ranks = league.replacement_rank
        assert ranks[Position.QB] == 12
        assert ranks[Position.RB] == 29
        assert ranks[Position.WR] == 29
        assert ranks[Position.TE] == 14

    def test_superflex_deepens_quarterback_replacement(
        self, superflex_league: LeagueSettings
    ) -> None:
        """A superflex slot is filled by a QB almost every week."""
        assert superflex_league.is_superflex
        assert superflex_league.replacement_rank[Position.QB] >= 20

    def test_three_receiver_league_deepens_receiver_replacement(self) -> None:
        settings = LeagueSettings.from_tokens(
            team_count=10,
            roster_position_tokens=["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "BN"],
        )
        assert settings.replacement_rank[Position.WR] == 35

    def test_two_quarterback_league(self) -> None:
        settings = LeagueSettings.from_tokens(
            team_count=12,
            roster_position_tokens=["QB", "QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"],
        )
        assert settings.replacement_rank[Position.QB] == 24

    def test_kickers_and_defenses_get_a_baseline(self, league: LeagueSettings) -> None:
        """Regression: a position without a baseline scores its whole projection."""
        assert league.replacement_rank[Position.K] == 12
        assert league.replacement_rank[Position.DEF] == 12

    def test_position_the_league_never_starts_floors_to_one(self) -> None:
        settings = LeagueSettings.from_tokens(
            team_count=12,
            roster_position_tokens=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"],
        )
        assert settings.replacement_rank[Position.K] == 1

    def test_flex_allocation_follows_dedicated_slots(self, league: LeagueSettings) -> None:
        allocation = league.flex_allocation
        assert allocation[Position.RB] == pytest.approx(0.4)
        assert allocation[Position.WR] == pytest.approx(0.4)
        assert allocation[Position.TE] == pytest.approx(0.2)


class TestPickOrdering:
    def test_snake_reverses_on_even_rounds(self, league: LeagueSettings) -> None:
        assert league.pick_number(draft_slot=1, round_number=1) == 1
        assert league.pick_number(draft_slot=1, round_number=2) == 24
        assert league.pick_number(draft_slot=12, round_number=1) == 12
        assert league.pick_number(draft_slot=12, round_number=2) == 13

    def test_linear_does_not_reverse(self) -> None:
        settings = LeagueSettings.from_tokens(
            team_count=10,
            roster_position_tokens=["QB", "RB", "WR"],
            draft_type=DraftType.LINEAR,
        )
        assert settings.pick_number(draft_slot=1, round_number=2) == 11
        assert settings.pick_number(draft_slot=10, round_number=2) == 20

    def test_auction_has_no_pick_order(self) -> None:
        settings = LeagueSettings.from_tokens(
            team_count=10,
            roster_position_tokens=["QB", "RB"],
            draft_type=DraftType.AUCTION,
        )
        with pytest.raises(LeagueConfigurationError, match="auction"):
            settings.pick_number(draft_slot=1, round_number=1)

    def test_picks_for_slot_snake_pattern(self, league: LeagueSettings) -> None:
        assert league.picks_for_slot(5)[:4] == (5, 20, 29, 44)

    def test_picks_until_next_turn(self, league: LeagueSettings) -> None:
        assert league.picks_until_next_turn(draft_slot=5, current_pick=5) == 0
        assert league.picks_until_next_turn(draft_slot=5, current_pick=6) == 14

    def test_no_picks_left_returns_none(self, league: LeagueSettings) -> None:
        last = league.picks_for_slot(5)[-1]
        assert league.next_pick_for_slot(5, after_pick=last) is None


class TestValidation:
    @pytest.mark.parametrize("team_count", [0, 1, 33])
    def test_rejects_impossible_team_counts(self, team_count: int) -> None:
        with pytest.raises(LeagueConfigurationError):
            LeagueSettings.from_tokens(team_count=team_count, roster_position_tokens=["QB", "BN"])

    def test_rejects_empty_roster(self) -> None:
        with pytest.raises(LeagueConfigurationError, match="roster_slots"):
            LeagueSettings.from_tokens(team_count=12, roster_position_tokens=[])

    def test_rejects_draft_slot_outside_league(self) -> None:
        with pytest.raises(LeagueConfigurationError, match="user_draft_slot"):
            LeagueSettings.from_tokens(
                team_count=12, roster_position_tokens=["QB", "BN"], user_draft_slot=13
            )

    def test_unrecognised_tokens_are_preserved_not_dropped(self) -> None:
        """An IDP league must surface a warning, not silently mis-size itself."""
        settings = LeagueSettings.from_tokens(
            team_count=12, roster_position_tokens=["QB", "RB", "IDP_FLEX", "BN"]
        )
        assert settings.unrecognised_slot_tokens == ("IDP_FLEX",)
        assert RosterSlot.UNKNOWN in settings.roster_slots


class TestScoringFormat:
    @pytest.mark.parametrize(
        ("raw", "ppr"),
        [("ppr", 1.0), ("half_ppr", 0.5), ("std", 0.0), ("dynasty_ppr", 1.0)],
    )
    def test_reception_values(self, raw: str, ppr: float) -> None:
        assert ScoringFormat.parse(raw).points_per_reception == ppr

    def test_unknown_format_defaults_to_half_ppr(self) -> None:
        assert ScoringFormat.parse("mystery") is ScoringFormat.HALF_PPR
