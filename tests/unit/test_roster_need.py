"""Tests for roster-need computation."""

from __future__ import annotations

import pytest

from fhe.core.draft.roster import compute_roster_need
from fhe.core.league import LeagueSettings
from fhe.core.types import Position, RosterSlot

pytestmark = pytest.mark.unit


class TestRosterNeed:
    def test_empty_roster_needs_every_starting_slot(self, league: LeagueSettings) -> None:
        need = compute_roster_need(league, [])
        assert len(need.unfilled_slots) == league.starters_per_team
        assert need.need_for(Position.RB) == 1.0
        assert need.need_for(Position.QB) == 1.0

    def test_filling_dedicated_slots_drops_need_to_flex_level(self, league: LeagueSettings) -> None:
        need = compute_roster_need(league, [Position.RB, Position.RB])
        assert RosterSlot.RB not in need.unfilled_slots
        assert need.need_for(Position.RB) < 1.0
        assert need.need_for(Position.WR) == 1.0

    def test_flex_is_filled_by_a_surplus_player(self, league: LeagueSettings) -> None:
        drafted = [
            Position.QB,
            Position.RB,
            Position.RB,
            Position.WR,
            Position.WR,
            Position.TE,
            Position.RB,
        ]
        need = compute_roster_need(league, drafted)
        assert RosterSlot.FLEX not in need.unfilled_slots

    def test_dedicated_slots_are_filled_before_flex(self, league: LeagueSettings) -> None:
        """Filling FLEX first would strand a back there and report a phantom need."""
        need = compute_roster_need(league, [Position.RB, Position.RB, Position.RB])
        # Two dedicated RB slots consumed, the third back takes the flex.
        assert RosterSlot.RB not in need.unfilled_slots
        assert RosterSlot.FLEX not in need.unfilled_slots
        assert RosterSlot.WR in need.unfilled_slots

    def test_stacking_a_position_suppresses_further_need(self, league: LeagueSettings) -> None:
        need = compute_roster_need(league, [Position.TE] * 4)
        assert need.need_for(Position.TE) < 0.1
        assert need.need_for(Position.RB) == 1.0

    def test_complete_starting_lineup_leaves_only_depth_need(self, league: LeagueSettings) -> None:
        drafted = [
            Position.QB,
            Position.RB,
            Position.RB,
            Position.WR,
            Position.WR,
            Position.TE,
            Position.WR,
            Position.K,
            Position.DEF,
        ]
        need = compute_roster_need(league, drafted)
        assert need.unfilled_slots == ()
        assert need.starters_remaining == 0
        assert all(v <= 0.15 for v in need.need_by_position.values())

    def test_superflex_creates_a_second_quarterback_need(
        self, superflex_league: LeagueSettings
    ) -> None:
        need = compute_roster_need(superflex_league, [Position.QB])
        assert need.is_starter_slot_open_for(Position.QB)

    def test_position_counts_are_reported(self, league: LeagueSettings) -> None:
        need = compute_roster_need(league, [Position.RB, Position.RB, Position.WR])
        assert need.position_counts[Position.RB] == 2
        assert need.position_counts[Position.WR] == 1
