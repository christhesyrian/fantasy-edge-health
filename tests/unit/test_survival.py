"""Tests for next-pick survival probability."""

from __future__ import annotations

import pytest

from fhe.core.draft.survival import (
    default_sigma,
    survival_probability,
    take_now_probability,
)

pytestmark = pytest.mark.unit


class TestSurvivalProbability:
    def test_survival_decreases_monotonically_with_distance(self) -> None:
        raw = [
            survival_probability(adp=30.0, current_pick=10, next_pick=n)
            for n in (12, 15, 20, 25, 30, 40)
        ]
        assert all(p is not None for p in raw)
        probabilities = [p for p in raw if p is not None]
        assert probabilities == sorted(probabilities, reverse=True)

    def test_player_is_available_when_the_user_is_on_the_clock(self) -> None:
        assert survival_probability(adp=30.0, current_pick=10, next_pick=10) == 1.0
        assert survival_probability(adp=30.0, current_pick=10, next_pick=9) == 1.0

    def test_near_certain_survival_just_past_a_late_adp(self) -> None:
        result = survival_probability(adp=120.0, current_pick=10, next_pick=12)
        assert result is not None
        assert result > 0.98

    def test_near_certain_loss_far_past_an_early_adp(self) -> None:
        result = survival_probability(adp=5.0, current_pick=1, next_pick=30)
        assert result is not None
        assert result < 0.02

    def test_tighter_dispersion_sharpens_the_estimate(self) -> None:
        wide = survival_probability(adp=30.0, current_pick=10, next_pick=35, adp_stdev=20.0)
        tight = survival_probability(adp=30.0, current_pick=10, next_pick=35, adp_stdev=2.0)
        assert wide is not None and tight is not None
        assert tight < wide

    def test_missing_inputs_return_none_rather_than_a_guess(self) -> None:
        assert survival_probability(adp=None, current_pick=10, next_pick=20) is None
        assert survival_probability(adp=30.0, current_pick=10, next_pick=None) is None

    def test_result_is_always_a_probability(self) -> None:
        for adp in (1.0, 30.0, 100.0, 250.0):
            for current in (1, 25, 90):
                for nxt in (current + 1, current + 12, current + 60):
                    value = survival_probability(adp=adp, current_pick=current, next_pick=nxt)
                    assert value is not None
                    assert 0.0 <= value <= 1.0


class TestFallerReAnchoring:
    def test_a_faller_is_not_reported_as_certain_to_vanish(self) -> None:
        """Regression: the unconditional model gave a player 25 picks past ADP a
        ~0.01% survival chance, which is obviously wrong on screen."""
        result = survival_probability(adp=20.0, current_pick=45, next_pick=57)
        assert result is not None
        assert result > 0.25

    def test_survival_recovers_as_a_player_falls_further(self) -> None:
        """Players who fall tend to keep falling; the estimate must reflect that."""
        at_adp = survival_probability(adp=20.0, current_pick=20, next_pick=32)
        well_past = survival_probability(adp=20.0, current_pick=60, next_pick=72)
        assert at_adp is not None and well_past is not None
        assert well_past > at_adp

    def test_re_anchoring_does_not_disturb_the_normal_case(self) -> None:
        """A player still ahead of their ADP is scored by the plain model."""
        result = survival_probability(adp=30.0, current_pick=10, next_pick=20)
        assert result is not None
        assert 0.85 < result < 0.90


class TestSigma:
    def test_dispersion_grows_with_adp(self) -> None:
        assert default_sigma(10.0) < default_sigma(60.0) < default_sigma(150.0)

    def test_dispersion_is_bounded(self) -> None:
        assert default_sigma(0.5) >= 3.0
        assert default_sigma(1000.0) <= 40.0


class TestTakeNowProbability:
    def test_is_the_complement_of_survival(self) -> None:
        survival = survival_probability(adp=30.0, current_pick=10, next_pick=25)
        take_now = take_now_probability(survival)
        assert survival is not None and take_now is not None
        assert survival + take_now == pytest.approx(1.0, abs=1e-4)

    def test_propagates_missing_input(self) -> None:
        assert take_now_probability(None) is None
