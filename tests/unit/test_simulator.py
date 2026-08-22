"""Tests for the mock draft simulator.

The simulator's contract is that it exercises the production draft path, and
that a seed reproduces a draft exactly. Both are load-bearing: the first makes a
mock draft a real rehearsal, the second makes it usable in automated tests.
"""

from __future__ import annotations

import pytest

from fhe.core.draft.models import DraftablePlayer
from fhe.core.draft.service import evaluate_draft
from fhe.core.errors import DraftStateError
from fhe.core.league import LeagueSettings
from fhe.core.simulation import MockDraftSimulator, SimulationConfig
from fhe.core.types import Position

pytestmark = pytest.mark.unit


@pytest.fixture
def simulator(
    league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
) -> MockDraftSimulator:
    return MockDraftSimulator(league, player_pool, config=SimulationConfig(seed=42))


class TestDeterminism:
    def test_the_same_seed_reproduces_the_same_draft(
        self, league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        def run() -> list[str]:
            sim = MockDraftSimulator(
                league, player_pool, config=SimulationConfig(seed=99), user_draft_slot=None
            )
            while not sim.is_complete:
                if sim.advance() is None:
                    break
            return [p.player_uuid for p in sim.state.picks]

        assert run() == run()

    def test_different_seeds_produce_different_drafts(
        self, league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        def run(seed: int) -> list[str]:
            sim = MockDraftSimulator(
                league, player_pool, config=SimulationConfig(seed=seed), user_draft_slot=None
            )
            for _ in range(40):
                if sim.advance() is None:
                    break
            return [p.player_uuid for p in sim.state.picks]

        assert run(1) != run(2)

    def test_reset_restores_the_original_sequence(
        self, league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        sim = MockDraftSimulator(
            league, player_pool, config=SimulationConfig(seed=5), user_draft_slot=None
        )
        for _ in range(20):
            sim.advance()
        first = [p.player_uuid for p in sim.state.picks]

        sim.reset()
        assert sim.state.pick_count == 0
        for _ in range(20):
            sim.advance()
        assert [p.player_uuid for p in sim.state.picks] == first


class TestTurnHandling:
    def test_simulator_stops_at_the_users_turn(self, simulator: MockDraftSimulator) -> None:
        simulator.advance_to_user_turn()
        assert simulator.is_user_on_the_clock
        assert simulator.advance() is None

    def test_user_pick_advances_the_draft(self, simulator: MockDraftSimulator) -> None:
        simulator.advance_to_user_turn()
        before = simulator.state.pick_count
        target = simulator.available[0]
        simulator.draft_player(target.player_uuid)

        assert simulator.state.pick_count == before + 1
        assert simulator.state.is_drafted(target.player_uuid)
        assert not simulator.is_user_on_the_clock

    def test_cannot_pick_out_of_turn(self, simulator: MockDraftSimulator) -> None:
        assert not simulator.is_user_on_the_clock
        with pytest.raises(DraftStateError, match="not the user's turn"):
            simulator.draft_player(simulator.available[0].player_uuid)

    def test_cannot_draft_an_already_taken_player(self, simulator: MockDraftSimulator) -> None:
        simulator.advance_to_user_turn()
        taken = simulator.state.picks[0].player_uuid
        with pytest.raises(DraftStateError, match="already been drafted"):
            simulator.draft_player(taken)

    def test_cannot_draft_an_unknown_player(self, simulator: MockDraftSimulator) -> None:
        simulator.advance_to_user_turn()
        with pytest.raises(DraftStateError, match="unknown player"):
            simulator.draft_player("not-a-real-player")


class TestFullDraft:
    def test_a_complete_draft_fills_every_pick_exactly_once(
        self, league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        sim = MockDraftSimulator(
            league, player_pool, config=SimulationConfig(seed=11), user_draft_slot=None
        )
        while not sim.is_complete and sim.advance() is not None:
            pass

        assert sim.is_complete
        assert sim.state.pick_count == league.total_picks

        pick_numbers = [p.pick_no for p in sim.state.picks]
        assert pick_numbers == list(range(1, league.total_picks + 1))
        players = [p.player_uuid for p in sim.state.picks]
        assert len(set(players)) == len(players), "a player was drafted twice"

    def test_every_team_ends_with_a_full_roster(
        self, league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        sim = MockDraftSimulator(
            league, player_pool, config=SimulationConfig(seed=12), user_draft_slot=None
        )
        while not sim.is_complete and sim.advance() is not None:
            pass

        for roster in sim.state.rosters:
            assert len(roster.player_uuids) == league.total_rounds

    def test_opponents_do_not_take_kickers_early(
        self, league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        """A room that drafts a kicker in round two is not a useful rehearsal."""
        by_uuid = {p.player_uuid: p for p in player_pool}
        sim = MockDraftSimulator(
            league, player_pool, config=SimulationConfig(seed=13), user_draft_slot=None
        )
        while not sim.is_complete and sim.advance() is not None:
            pass

        for pick in sim.state.picks[: league.team_count * 8]:
            assert by_uuid[pick.player_uuid].position not in {Position.K, Position.DEF}


class TestEngineIntegration:
    def test_recommendations_change_as_picks_are_made(
        self, simulator: MockDraftSimulator, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        """The whole point: the board must react to the room."""
        simulator.advance_to_user_turn()
        first = evaluate_draft(simulator.state, player_pool, user_draft_slot=5)
        top_before = [r.player_uuid for r in first.recommendations[:5]]

        assert first.best_pick is not None
        simulator.draft_player(first.best_pick.player_uuid)
        for _ in range(14):
            simulator.advance()

        second = evaluate_draft(simulator.state, player_pool, user_draft_slot=5)
        top_after = [r.player_uuid for r in second.recommendations[:5]]

        assert top_before != top_after

    def test_drafted_players_disappear_from_the_board(
        self, simulator: MockDraftSimulator, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        simulator.advance_to_user_turn()
        board = evaluate_draft(simulator.state, player_pool, user_draft_slot=5)
        drafted = simulator.state.drafted_player_uuids

        assert drafted
        assert not drafted & {r.player_uuid for r in board.recommendations}

    def test_roster_need_updates_after_the_user_picks(
        self, simulator: MockDraftSimulator, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        by_uuid = {p.player_uuid: p for p in player_pool}
        simulator.advance_to_user_turn()
        board = evaluate_draft(simulator.state, player_pool, user_draft_slot=5)
        chosen = board.best_pick
        assert chosen is not None
        simulator.draft_player(chosen.player_uuid)

        roster_positions = {by_uuid[u].position for u in simulator.state.roster(5).player_uuids}
        assert by_uuid[chosen.player_uuid].position in roster_positions

    def test_a_full_user_draft_produces_a_legal_roster(
        self, league: LeagueSettings, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        """End-to-end: let the engine draft for the user the whole way through."""
        sim = MockDraftSimulator(league, player_pool, config=SimulationConfig(seed=21))
        by_uuid = {p.player_uuid: p for p in player_pool}

        while not sim.is_complete:
            if sim.is_user_on_the_clock:
                board = evaluate_draft(sim.state, player_pool, user_draft_slot=5)
                assert board.best_pick is not None
                sim.draft_player(board.best_pick.player_uuid)
            elif sim.advance() is None:
                break

        roster = sim.state.roster(5)
        assert len(roster.player_uuids) == league.total_rounds

        positions = [by_uuid[u].position for u in roster.player_uuids]
        # A legal lineup needs at least one of each dedicated starting position.
        for required in (Position.QB, Position.RB, Position.WR, Position.TE):
            assert required in positions, f"engine never drafted a {required.value}"
