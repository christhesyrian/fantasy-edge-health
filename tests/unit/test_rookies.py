"""Rookie landing-spot opportunity."""

from __future__ import annotations

import pytest

from fhe.core.rookies import RANKED_TEAMS, RookieOpportunity

pytestmark = pytest.mark.unit


def landing(**overrides: object) -> RookieOpportunity:
    base: dict[str, object] = {
        "team": "ARI",
        "coach": "Jonathan Gannon",
        "seasons_under_coach": 3,
        "average_rookie_touches": 96.7,
        "rank": 15,
        "teams_ranked": 25,
        "had_recent_workhorse": False,
    }
    base.update(overrides)
    return RookieOpportunity(**base)  # type: ignore[arg-type]


class TestUnknownStaff:
    def test_a_new_coach_produces_no_boost(self) -> None:
        """And no penalty either.

        A coach who has not yet coached the team is an unknown. Inventing a
        verdict about him — in either direction — is exactly the failure this
        product exists to avoid.
        """
        new_coach = landing(seasons_under_coach=0, average_rookie_touches=None, rank=None)

        assert not new_coach.is_measured
        assert new_coach.boost == 0.0

    def test_a_team_outside_the_ranked_range_gets_nothing(self) -> None:
        """Absence of evidence for rookies is not evidence against one."""
        assert landing(rank=RANKED_TEAMS + 1).boost == 0.0


class TestBoost:
    def test_the_top_ranked_staff_earns_the_most(self) -> None:
        assert landing(rank=1).boost > landing(rank=10).boost

    def test_the_boost_decays_down_the_order(self) -> None:
        """Descending, as the ranking is meant to express."""
        boosts = [landing(rank=r).boost for r in (1, 5, 10, 20, 30)]

        assert boosts == sorted(boosts, reverse=True)
        assert all(b > 0 for b in boosts)

    def test_decay_is_linear_rather_than_steep(self) -> None:
        """A steeper curve would imply precision this measurement lacks."""
        first_step = landing(rank=1).boost - landing(rank=2).boost
        later_step = landing(rank=20).boost - landing(rank=21).boost

        # Rounded to two decimals, so equal steps can differ by a cent.
        assert first_step == pytest.approx(later_step, abs=0.02)

    def test_a_recent_workhorse_adds_on_top(self) -> None:
        """The most direct precedent available that the door is open."""
        without = landing(had_recent_workhorse=False).boost
        with_precedent = landing(had_recent_workhorse=True).boost

        assert with_precedent > without

    def test_the_boost_stays_smaller_than_measured_value(self) -> None:
        """It separates rookies from each other, not rookies from veterans.

        A first-year player is the least-known player in the draft. When the
        maximum award here rivalled the value-over-replacement spread at the top
        of the board, a signal about the *least* certain players outweighed
        measured value — and a rookie at ADP 41 outranked Derrick Henry at 36.
        """
        from fhe.core.draft.engine import W_VORP

        best_possible = landing(rank=1, had_recent_workhorse=True).boost

        # An order of magnitude below the primary signal. The landing spot
        # describes a situation, not a player, and must stay a tiebreaker
        # beside measured value rather than competing with it. The value that
        # caused the regression was 8.5 against this bound of 4.0.
        assert best_possible < W_VORP / 10, (
            "the landing spot must stay a tiebreaker beside projected value"
        )

    def test_the_boost_is_never_negative(self) -> None:
        """This signal only ever moves a rookie up."""
        for rank in (1, 15, 30, 31, None):
            assert landing(rank=rank).boost >= 0.0
