"""Draft state: idempotency and ordering guarantees.

These tests encode the live-draft quality bar. Polling a provider means the same
pick arrives repeatedly, sometimes out of order, sometimes several at once - and
none of that may corrupt the board.
"""

from __future__ import annotations

import pytest

from fhe.core.draft.models import DraftPick, PickOutcome
from fhe.core.draft.state import DraftState
from fhe.core.errors import DraftStateError
from fhe.core.league import LeagueSettings

pytestmark = pytest.mark.unit


def pick(no: int, slot: int, player: str) -> DraftPick:
    """Build a pick for a 12-team league."""
    return DraftPick(
        pick_no=no,
        round_number=(no - 1) // 12 + 1,
        draft_slot=slot,
        player_uuid=player,
        roster_id=slot,
    )


class TestIdempotency:
    def test_replaying_the_same_pick_changes_nothing(self, league: LeagueSettings) -> None:
        state = DraftState(league)
        first = state.apply_pick(pick(1, 1, "a"))
        second = state.apply_pick(pick(1, 1, "a"))

        assert first.outcome is PickOutcome.APPLIED
        assert second.outcome is PickOutcome.DUPLICATE
        assert state.pick_count == 1
        assert state.roster(1).player_uuids == ("a",)

    def test_replaying_a_whole_payload_is_a_no_op(self, league: LeagueSettings) -> None:
        """The realistic polling case: the provider resends everything each time."""
        payload = [pick(n, n, f"p{n}") for n in range(1, 13)]
        state = DraftState(league)

        state.apply_picks(payload)
        snapshot = state.picks
        results = state.apply_picks(payload)

        assert all(r.outcome is PickOutcome.DUPLICATE for r in results)
        assert state.picks == snapshot
        assert state.pick_count == 12

    def test_same_pick_number_with_a_different_player_is_a_conflict(
        self, league: LeagueSettings
    ) -> None:
        state = DraftState(league)
        state.apply_pick(pick(1, 1, "a"))
        result = state.apply_pick(pick(1, 1, "b"))

        assert result.outcome is PickOutcome.CONFLICT
        assert result.existing is not None
        assert result.existing.player_uuid == "a"
        # State is untouched: history is never silently overwritten.
        assert state.picks[0].player_uuid == "a"
        assert state.pick_count == 1

    def test_same_player_at_a_different_pick_number_is_a_conflict(
        self, league: LeagueSettings
    ) -> None:
        """A player cannot be drafted twice, even at a different pick."""
        state = DraftState(league)
        state.apply_pick(pick(1, 1, "a"))
        result = state.apply_pick(pick(2, 2, "a"))

        assert result.outcome is PickOutcome.CONFLICT
        assert state.pick_count == 1


class TestOrdering:
    def test_out_of_order_arrival_yields_ordered_state(self, league: LeagueSettings) -> None:
        state = DraftState(league)
        state.apply_picks([pick(3, 3, "c"), pick(1, 1, "a"), pick(2, 2, "b")])

        assert [p.pick_no for p in state.picks] == [1, 2, 3]

    def test_arrival_order_does_not_affect_final_state(self, league: LeagueSettings) -> None:
        """Multiple picks between polls must land identically however they arrive."""
        batch = [pick(n, n, f"p{n}") for n in range(1, 8)]
        forward = DraftState(league)
        forward.apply_picks(batch)
        backward = DraftState(league)
        backward.apply_picks(list(reversed(batch)))

        assert forward.picks == backward.picks
        assert [r.player_uuids for r in forward.rosters] == [
            r.player_uuids for r in backward.rosters
        ]

    def test_current_pick_is_the_lowest_unfilled_number(self, league: LeagueSettings) -> None:
        """Gaps are real while picks are still in flight; count+1 would be wrong."""
        state = DraftState(league)
        state.apply_picks([pick(1, 1, "a"), pick(3, 3, "c")])
        assert state.current_pick_number == 2


class TestTurnMaths:
    def test_picks_until_turn_for_user_slot(self, league: LeagueSettings) -> None:
        state = DraftState(league)
        assert state.picks_until_slot_turn(5) == 4

        state.apply_picks([pick(n, n, f"p{n}") for n in range(1, 5)])
        assert state.picks_until_slot_turn(5) == 0

    def test_next_pick_number_follows_the_snake(self, league: LeagueSettings) -> None:
        state = DraftState(league)
        state.apply_picks([pick(n, n, f"p{n}") for n in range(1, 6)])
        assert state.next_pick_number_for_slot(5) == 20

    def test_completed_draft_reports_no_current_pick(self) -> None:
        tiny = LeagueSettings.from_tokens(
            team_count=2, roster_position_tokens=["QB"], user_draft_slot=1
        )
        state = DraftState(tiny)
        state.apply_picks([pick(1, 1, "a"), DraftPick(2, 1, 2, "b")])

        assert state.is_complete
        assert state.current_pick_number is None
        assert state.picks_until_slot_turn(1) is None


class TestValidation:
    def test_pick_number_outside_the_draft_is_rejected(self, league: LeagueSettings) -> None:
        state = DraftState(league)
        with pytest.raises(DraftStateError, match="pick_no"):
            state.apply_pick(pick(999, 1, "a"))

    def test_unknown_draft_slot_is_rejected(self, league: LeagueSettings) -> None:
        state = DraftState(league)
        with pytest.raises(DraftStateError, match="draft_slot"):
            state.apply_pick(pick(1, 99, "a"))

    def test_rebuild_from_picks_matches_incremental_application(
        self, league: LeagueSettings
    ) -> None:
        """Reconnect path: rebuilding from a full pick list must be equivalent."""
        batch = [pick(n, n, f"p{n}") for n in range(1, 10)]
        incremental = DraftState(league)
        for p in batch:
            incremental.apply_pick(p)
        rebuilt = DraftState.from_picks(league, batch)

        assert rebuilt.picks == incremental.picks
        assert rebuilt.drafted_player_uuids == incremental.drafted_player_uuids


class TestTradedPicksAndKeepers:
    def test_traded_pick_credits_the_acquiring_roster(self, league: LeagueSettings) -> None:
        """draft_slot owns the position; roster_id owns the selection."""
        state = DraftState(league)
        traded = DraftPick(pick_no=1, round_number=1, draft_slot=1, player_uuid="a", roster_id=7)
        state.apply_pick(traded)

        assert state.picks[0].roster_id == 7
        assert state.picks[0].draft_slot == 1

    def test_keeper_is_applied_like_any_other_pick(self, league: LeagueSettings) -> None:
        state = DraftState(league)
        keeper = DraftPick(pick_no=1, round_number=1, draft_slot=1, player_uuid="a", is_keeper=True)
        assert state.apply_pick(keeper).outcome is PickOutcome.APPLIED
        assert state.picks[0].is_keeper
        assert state.is_drafted("a")
