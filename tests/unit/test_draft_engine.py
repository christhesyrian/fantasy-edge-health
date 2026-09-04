"""Tests for the recommendation engine and board assembly.

The behavioural assertions here are the ones that would embarrass the product if
they broke: kickers in round one, a defense outranking a starting back, or a
score that cannot be reconciled with its own explanation.
"""

from __future__ import annotations

import itertools

import pytest

from fhe.core.draft.board import AlertLevel, build_board
from fhe.core.draft.engine import DraftContext, rank_board
from fhe.core.draft.models import DraftablePlayer
from fhe.core.draft.roster import compute_roster_need
from fhe.core.draft.scarcity import build_tiers, compute_scarcity
from fhe.core.draft.service import evaluate_draft
from fhe.core.draft.state import DraftState
from fhe.core.draft.vorp import (
    ReplacementBaseline,
    compute_replacement_baseline,
    value_over_replacement,
)
from fhe.core.league import LeagueSettings
from fhe.core.types import (
    DraftType,
    InjuryDesignation,
    Position,
    Recommendation,
    ScoringFormat,
)
from tests.conftest import make_player

pytestmark = pytest.mark.unit


def league_ppr() -> LeagueSettings:
    """A conventional 12-team PPR league."""
    return LeagueSettings.from_tokens(
        team_count=12,
        roster_position_tokens=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"],
        scoring_format=ScoringFormat.PPR,
        draft_type=DraftType.SNAKE,
    )


def build_context(
    players: list[DraftablePlayer],
    league: LeagueSettings,
    *,
    drafted_positions: list[Position] | None = None,
    current_pick: int = 1,
    next_user_pick: int | None = 20,
    user_picks_remaining: int | None = None,
) -> DraftContext:
    """Assemble a context directly, for tests that need precise control."""
    baseline = compute_replacement_baseline(players, league)
    need = compute_roster_need(league, drafted_positions or [])
    scarcity = compute_scarcity(
        players,
        picks_until_next_turn=(next_user_pick - current_pick) if next_user_pick else None,
        replacement_points=baseline.points_by_position,
    )
    return DraftContext(
        available=players,
        baseline=baseline,
        scarcity=scarcity,
        roster_need=need,
        current_pick=current_pick,
        next_user_pick=next_user_pick,
        user_picks_remaining=user_picks_remaining,
    )


class TestExplainability:
    def test_components_reconcile_with_the_overall_score(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        """A displayed score must always equal the arithmetic shown beneath it."""
        board = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        for rec in board.recommendations[:40]:
            total = round(sum(c.points for c in rec.components), 2)
            assert total == pytest.approx(rec.overall_score, abs=0.01), rec.name

    def test_every_recommendation_carries_reasons(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        board = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        for rec in board.recommendations[:25]:
            assert rec.reasons, f"{rec.name} has no reasons"
            assert all(r.strip() for r in rec.reasons)

    def test_components_are_ordered_by_magnitude(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        board = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        rec = board.recommendations[0]
        magnitudes = [abs(c.points) for c in rec.components]
        assert magnitudes == sorted(magnitudes, reverse=True)


class TestPositionalSanity:
    def test_kickers_and_defenses_are_not_drafted_early(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        """Regression: missing baselines once put a kicker at rank 3 overall."""
        board = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        late = [r for r in board.recommendations if r.position in {Position.K, Position.DEF}]
        assert late, "pool should contain kickers and defenses"
        assert min(r.model_rank for r in late) > 100

    def test_late_round_positions_surface_at_the_end(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        """With one pick left and an empty kicker slot, a kicker should lead."""
        state = DraftState(league)
        players = list(player_pool)
        # Consume the board up to the user's final pick.
        final_pick = league.picks_for_slot(5)[-1]
        for taken, pick_no in enumerate(range(1, final_pick)):
            slot = ((pick_no - 1) % league.team_count) + 1
            if (pick_no - 1) // league.team_count % 2 == 1:
                slot = league.team_count - slot + 1
            from fhe.core.draft.models import DraftPick

            state.apply_pick(
                DraftPick(
                    pick_no=pick_no,
                    round_number=(pick_no - 1) // league.team_count + 1,
                    draft_slot=slot,
                    player_uuid=players[taken].player_uuid,
                )
            )

        board = evaluate_draft(state, player_pool, user_draft_slot=5)
        assert board.best_pick is not None
        assert board.best_pick.position in {Position.K, Position.DEF}

    def test_a_replacement_level_player_scores_far_below_an_elite_one(
        self, league: LeagueSettings
    ) -> None:
        players = [
            make_player("elite", Position.RB, projected_points=340.0, adp=2.0),
            *[
                make_player(f"rb{i}", Position.RB, projected_points=150.0 - i, adp=60.0 + i)
                for i in range(40)
            ],
        ]
        recs = rank_board(build_context(players, league))
        assert recs[0].player_uuid == "elite"
        assert recs[0].overall_score > recs[-1].overall_score + 20

    def test_a_deeply_sub_replacement_player_cannot_win_on_market_signal(
        self, league: LeagueSettings
    ) -> None:
        """A faller far below replacement must not outrank the best at his slot.

        Regression: the value component was clamped at zero, so every
        sub-replacement player scored an identical 0.0 on the heaviest term and
        the ADP term — which rewards falling — decided their order. A
        quarterback 124 points below the baseline outranked QB1.
        """
        best = make_player("qb-best", Position.QB, projected_points=395.0, adp=30.0)
        # Deep bench quarterback the market has all but forgotten.
        forgotten = make_player("qb-forgotten", Position.QB, projected_points=190.0, adp=263.0)
        filler = [
            make_player(f"qb{i}", Position.QB, projected_points=380.0 - i * 7.0, adp=40.0 + i)
            for i in range(30)
        ]
        recs = rank_board(build_context([best, forgotten, *filler], league))
        by_uuid = {r.player_uuid: r for r in recs}

        assert by_uuid["qb-forgotten"].adp_value is not None
        assert by_uuid["qb-forgotten"].adp_value > 0, "the setup must give him a faller bonus"
        assert by_uuid["qb-best"].overall_score > by_uuid["qb-forgotten"].overall_score

    def test_value_component_is_signed_below_replacement(self, league: LeagueSettings) -> None:
        """Being below replacement costs points rather than merely earning none."""
        players = [
            make_player(f"rb{i}", Position.RB, projected_points=300.0 - i * 5.0, adp=float(i + 1))
            for i in range(40)
        ]
        recs = rank_board(build_context(players, league))
        worst = recs[-1]
        value = next(c for c in worst.components if c.name == "value_over_replacement")

        assert worst.vorp is not None and worst.vorp < 0
        assert value.points < 0
        assert "below" in (value.detail or "")

    def test_a_player_with_no_projection_earns_no_adp_value(self) -> None:
        """Regression: value cannot be manufactured out of ignorance.

        `adp_value` compares the market against *this model's* rank. Without a
        projection that rank says nothing about the player, so crediting the
        difference let a player nobody drafts until pick 460 — ranked highly
        only because nothing was known about him — score a maximal "the market
        undervalues him" bonus and climb the board on it.
        """
        league = LeagueSettings.from_tokens(
            team_count=12,
            roster_position_tokens=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN", "BN"],
            scoring_format=ScoringFormat.PPR,
            draft_type=DraftType.SNAKE,
        )
        # Deep, undrafted, and entirely unknown to us.
        unknown = make_player("unknown", Position.WR, projected_points=None, adp=460.0)
        known = [
            make_player(f"wr{i}", Position.WR, projected_points=260.0 - i * 4, adp=float(i + 1))
            for i in range(40)
        ]

        recs = rank_board(build_context([unknown, *known], league))
        by_uuid = {r.player_uuid: r for r in recs}

        assert by_uuid["unknown"].adp_value is None
        assert not [c for c in by_uuid["unknown"].components if c.name == "adp_value"]
        # And he must not outrank players we actually have a valuation for.
        assert by_uuid["unknown"].overall_score < by_uuid["wr0"].overall_score

    def test_a_projected_player_still_earns_adp_value(self) -> None:
        """The fix must not disable the signal where it is meaningful."""
        faller = make_player("faller", Position.WR, projected_points=300.0, adp=90.0)
        filler = [
            make_player(f"wr{i}", Position.WR, projected_points=180.0 - i, adp=float(i + 1))
            for i in range(40)
        ]

        recs = rank_board(build_context([faller, *filler], league_ppr()))
        by_uuid = {r.player_uuid: r for r in recs}

        assert by_uuid["faller"].adp_value is not None
        assert by_uuid["faller"].adp_value > 0


class TestHealthAdjustment:
    def test_injury_risk_lowers_a_players_score(self, league: LeagueSettings) -> None:
        healthy = make_player("healthy", Position.WR, projected_points=250.0, adp=10.0)
        hurt = make_player(
            "hurt",
            Position.WR,
            projected_points=250.0,
            adp=10.0,
            risk=InjuryDesignation.IR,
        )
        others = [
            make_player(f"wr{i}", Position.WR, projected_points=200.0 - i, adp=30.0 + i)
            for i in range(40)
        ]
        recs = rank_board(build_context([healthy, hurt, *others], league))
        by_uuid = {r.player_uuid: r for r in recs}

        assert by_uuid["healthy"].overall_score > by_uuid["hurt"].overall_score
        assert by_uuid["hurt"].recommendation is Recommendation.AVOID

    def test_severe_risk_is_labelled_avoid(self, league: LeagueSettings) -> None:
        hurt = make_player(
            "hurt",
            Position.RB,
            projected_points=300.0,
            adp=1.0,
            risk=InjuryDesignation.IR,
        )
        recs = rank_board(build_context([hurt], league))
        assert recs[0].recommendation is Recommendation.AVOID

    def test_moderate_risk_is_labelled_discount(self, league: LeagueSettings) -> None:
        hurt = make_player(
            "hurt",
            Position.RB,
            projected_points=300.0,
            adp=1.0,
            risk=InjuryDesignation.OUT,
        )
        recs = rank_board(build_context([hurt], league))
        assert recs[0].recommendation is Recommendation.DISCOUNT_RISK


class TestAdpValue:
    def test_a_faller_earns_positive_adp_value(self, league: LeagueSettings) -> None:
        faller = make_player("faller", Position.WR, projected_points=300.0, adp=90.0)
        filler = [
            make_player(f"wr{i}", Position.WR, projected_points=180.0 - i, adp=float(i + 1))
            for i in range(40)
        ]
        recs = rank_board(build_context([faller, *filler], league))
        by_uuid = {r.player_uuid: r for r in recs}
        assert by_uuid["faller"].adp_value is not None
        assert by_uuid["faller"].adp_value > 0

    def test_missing_adp_yields_no_adp_value(self, league: LeagueSettings) -> None:
        player = make_player("p", Position.RB, projected_points=250.0, adp=None)
        recs = rank_board(build_context([player], league))
        assert recs[0].adp_value is None
        assert recs[0].market_adp is None


class TestMissingData:
    def test_a_player_without_a_projection_cannot_top_the_board(
        self, league: LeagueSettings
    ) -> None:
        """Unknown must never outrank measured."""
        unknown = make_player("unknown", Position.RB, projected_points=None, adp=1.0)
        known = make_player("known", Position.RB, projected_points=320.0, adp=40.0)
        filler = [
            make_player(f"rb{i}", Position.RB, projected_points=150.0 - i, adp=50.0 + i)
            for i in range(35)
        ]
        recs = rank_board(build_context([unknown, known, *filler], league))
        assert recs[0].player_uuid == "known"

    def test_vorp_is_none_without_a_projection(self, league: LeagueSettings) -> None:
        baseline = ReplacementBaseline(
            points_by_position={Position.RB: 100.0},
            replacement_rank={Position.RB: 24},
            players_considered=0,
        )
        player = make_player("p", Position.RB, projected_points=None)
        assert value_over_replacement(player, baseline) is None

    def test_empty_board_returns_no_recommendations(self, league: LeagueSettings) -> None:
        assert rank_board(build_context([], league)) == ()


class TestDeterminism:
    def test_identical_input_produces_identical_output(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        first = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        second = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        assert [r.player_uuid for r in first.recommendations] == [
            r.player_uuid for r in second.recommendations
        ]
        assert [r.overall_score for r in first.recommendations] == [
            r.overall_score for r in second.recommendations
        ]


class TestTiers:
    def test_tiers_are_contiguous_and_descending(
        self, player_pool: tuple[DraftablePlayer, ...]
    ) -> None:
        tiers = build_tiers(list(player_pool), Position.RB)
        assert tiers
        assert [t.tier for t in tiers] == list(range(1, len(tiers) + 1))
        for earlier, later in itertools.pairwise(tiers):
            assert earlier.bottom_points >= later.top_points

    def test_a_flat_pool_is_a_single_tier(self) -> None:
        players = [
            make_player(f"rb{i}", Position.RB, projected_points=200.0 - i * 0.1) for i in range(20)
        ]
        assert len(build_tiers(players, Position.RB)) == 1

    def test_a_cliff_creates_a_boundary(self) -> None:
        players = [
            *[make_player(f"top{i}", Position.RB, projected_points=300.0 - i) for i in range(3)],
            *[make_player(f"low{i}", Position.RB, projected_points=150.0 - i) for i in range(5)],
        ]
        tiers = build_tiers(players, Position.RB)
        assert len(tiers) >= 2
        assert tiers[0].size == 3


class TestBoard:
    def test_board_headlines_are_populated(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        board = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        assert board.best_pick is not None
        assert board.safest_pick is not None
        assert board.highest_upside is not None
        assert board.best_value is not None

    def test_safest_pick_is_low_risk(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        board = evaluate_draft(DraftState(league), player_pool, user_draft_slot=5)
        assert board.safest_pick is not None
        assert board.safest_pick.health_risk is not None
        assert board.safest_pick.health_risk <= 25.0

    def test_pick_approaching_alert_is_critical(
        self, player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
    ) -> None:
        scarcity = compute_scarcity(
            list(player_pool),
            picks_until_next_turn=2,
            replacement_points=compute_replacement_baseline(player_pool, league).points_by_position,
        )
        board = build_board([], list(player_pool), scarcity, picks_until_user_turn=2)
        assert any(
            a.key == "pick_approaching" and a.level is AlertLevel.CRITICAL for a in board.alerts
        )
