"""Measured opportunity and scoring volatility.

The rule these exist to protect: neither signal may inflate a projection, and
neither may invent a judgement where nothing was measured.
"""

from __future__ import annotations

import pytest

from fhe.core.usage import (
    MIN_GAMES_FOR_USAGE,
    UsageProfile,
)

pytestmark = pytest.mark.unit


def profile(**overrides: object) -> UsageProfile:
    """A measured season, overridable field by field."""
    base: dict[str, object] = {
        "season": 2025,
        "games_sampled": 16,
        "snap_share": 0.80,
        "touches_per_game": 18.0,
        "points_per_game": 15.0,
        "points_stdev": 5.0,
    }
    base.update(overrides)
    return UsageProfile(**base)  # type: ignore[arg-type]


class TestMeasurement:
    def test_a_short_sample_is_not_a_measurement(self) -> None:
        """A three-game sample describes a fluke, not a role."""
        assert not profile(games_sampled=MIN_GAMES_FOR_USAGE - 1).is_measured

    def test_no_sample_reports_unknown_rather_than_zero(self) -> None:
        """Absent must never read as idle — that invents risk from nothing."""
        empty = UsageProfile()

        assert empty.opportunity_support("WR") is None
        assert empty.production_support("WR", 300.0) is None
        assert empty.corroboration("WR", 300.0) is None
        assert empty.volatility is None


class TestOpportunitySupport:
    def test_the_threshold_is_position_relative(self) -> None:
        """55% of snaps is a normal committee back and a part-time receiver.

        A single global threshold read every receiver and tight end as fully
        corroborated and made the signal almost inert.
        """
        half = profile(snap_share=0.55)

        receiver_support = half.opportunity_support("WR")
        assert half.opportunity_support("RB") == pytest.approx(1.0)
        assert receiver_support is not None
        assert receiver_support < 1.0

    def test_a_full_time_role_is_fully_corroborated(self) -> None:
        assert profile(snap_share=0.95).opportunity_support("WR") == pytest.approx(1.0)

    def test_positions_whose_snaps_mean_nothing_are_not_judged(self) -> None:
        """A kicker plays a handful of snaps by design."""
        assert profile(snap_share=0.05).opportunity_support("K") is None
        assert profile(snap_share=0.05).opportunity_support("DEF") is None


class TestProductionSupport:
    def test_outscoring_the_projection_is_full_corroboration(self) -> None:
        """The case that made opportunity alone wrong.

        A receiver on 70% of snaps who already outscored the projection being
        asked of him is not resting on a step up — he has done it.
        """
        efficient = profile(snap_share=0.70, points_per_game=23.4)

        opportunity = efficient.opportunity_support("WR")
        assert opportunity is not None and opportunity < 1.0
        assert efficient.production_support("WR", 340.0) == pytest.approx(1.0)
        assert efficient.corroboration("WR", 340.0) == pytest.approx(1.0)

    def test_a_large_step_up_is_only_partly_corroborated(self) -> None:
        # 5.9 a game measured, against a projection asking for 12.2.
        stepping_up = profile(snap_share=0.22, points_per_game=5.9)

        support = stepping_up.corroboration("RB", 12.2 * 17)

        assert support is not None
        assert 0.4 < support < 0.6

    def test_either_signal_is_enough(self) -> None:
        """Requiring both would penalise the efficient and the heavily used."""
        heavy_but_unproductive = profile(snap_share=0.95, points_per_game=4.0)

        assert heavy_but_unproductive.corroboration("WR", 300.0) == pytest.approx(1.0)


class TestVolatility:
    def test_it_is_relative_to_the_mean(self) -> None:
        """Four points of swing means different things at 20 and at 6."""
        star = profile(points_per_game=20.0, points_stdev=4.0)
        role = profile(points_per_game=6.0, points_stdev=4.0)

        assert star.volatility == pytest.approx(0.2)
        assert role.volatility == pytest.approx(0.667, abs=0.01)

    def test_a_near_zero_scorer_reports_no_volatility(self) -> None:
        """Regression: a kicker averaging 0.04 points showed a volatility of 3.5.

        That is a denominator artefact, not boom-or-bust, and it ranked
        fourth-string players among the highest-upside picks.
        """
        barely_played = profile(points_per_game=0.04, points_stdev=0.14)

        assert barely_played.volatility is None
