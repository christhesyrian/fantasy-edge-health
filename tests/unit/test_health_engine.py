"""Tests for the heuristic availability-risk scorer."""

from __future__ import annotations

from datetime import date

import pytest

from fhe.core.health import (
    HealthInputs,
    InjuryHistoryEvent,
    WorkloadSummary,
    score_health,
)
from fhe.core.types import (
    BodyRegion,
    InjuryDesignation,
    Position,
    PracticeStatus,
    PracticeTrajectory,
)

pytestmark = pytest.mark.unit

AS_OF = date(2026, 8, 22)
SEASON = 2026


def inputs(**overrides: object) -> HealthInputs:
    """Build health inputs with sensible defaults."""
    base: dict[str, object] = {
        "player_uuid": "p1",
        "position": Position.WR,
        "as_of": AS_OF,
        "current_season": SEASON,
    }
    base.update(overrides)
    return HealthInputs(**base)  # type: ignore[arg-type]


class TestScoreBounds:
    def test_score_is_always_within_range(self) -> None:
        catastrophic = score_health(
            inputs(
                position=Position.RB,
                designation=InjuryDesignation.IR,
                age=36.0,
                practice_statuses=(PracticeStatus.DNP,) * 4,
                injury_history=tuple(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        region=BodyRegion.HAMSTRING,
                        raw_descriptor="Hamstring",
                        games_missed=6,
                    )
                    for _ in range(8)
                ),
            )
        )
        assert 0.0 <= catastrophic.risk_score <= 100.0
        assert catastrophic.raw_score > 100.0  # clamping is visible, not hidden

    def test_pristine_profile_scores_zero(self) -> None:
        assessment = score_health(
            inputs(
                age=24.0,
                years_experience=3,
                workload=WorkloadSummary(season=2025, games_played=17),
            )
        )
        assert assessment.risk_score == 0.0
        assert assessment.raw_score < 0.0  # durability credit is preserved

    def test_components_sum_to_the_raw_score(self) -> None:
        assessment = score_health(
            inputs(
                position=Position.RB,
                designation=InjuryDesignation.QUESTIONABLE,
                age=30.0,
                practice_statuses=(PracticeStatus.FULL, PracticeStatus.DNP),
            )
        )
        assert sum(c.points for c in assessment.components) == pytest.approx(
            assessment.raw_score, abs=0.05
        )


class TestDesignation:
    @pytest.mark.parametrize(
        ("designation", "minimum"),
        [
            (InjuryDesignation.IR, 70.0),
            (InjuryDesignation.PUP, 55.0),
            (InjuryDesignation.OUT, 40.0),
            (InjuryDesignation.QUESTIONABLE, 10.0),
        ],
    )
    def test_designations_are_ordered_by_severity(
        self, designation: InjuryDesignation, minimum: float
    ) -> None:
        assessment = score_health(inputs(designation=designation))
        assert assessment.risk_score >= minimum

    def test_injured_reserve_reads_as_severe(self) -> None:
        assert score_health(inputs(designation=InjuryDesignation.IR)).risk_band == "SEVERE"

    def test_no_designation_adds_no_risk(self) -> None:
        assessment = score_health(inputs(designation=InjuryDesignation.ACTIVE))
        assert not any(c.name == "current_designation" for c in assessment.components)


class TestPractice:
    def test_worsening_practice_adds_risk(self) -> None:
        improving = score_health(
            inputs(
                designation=InjuryDesignation.QUESTIONABLE,
                practice_statuses=(PracticeStatus.DNP, PracticeStatus.LIMITED, PracticeStatus.FULL),
            )
        )
        worsening = score_health(
            inputs(
                designation=InjuryDesignation.QUESTIONABLE,
                practice_statuses=(PracticeStatus.FULL, PracticeStatus.LIMITED, PracticeStatus.DNP),
            )
        )
        assert worsening.risk_score > improving.risk_score
        assert worsening.practice_trajectory is PracticeTrajectory.WORSENING
        assert improving.practice_trajectory is PracticeTrajectory.IMPROVING

    def test_consecutive_missed_practices_are_counted(self) -> None:
        assessment = score_health(
            inputs(practice_statuses=(PracticeStatus.DNP, PracticeStatus.DNP))
        )
        assert any(c.name == "consecutive_dnp" for c in assessment.components)


class TestInjuryHistory:
    def test_recent_injuries_count_more_than_old_ones(self) -> None:
        recent = score_health(
            inputs(
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 1, region=BodyRegion.KNEE, raw_descriptor="Knee"
                    ),
                )
            )
        )
        old = score_health(
            inputs(
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 3, region=BodyRegion.KNEE, raw_descriptor="Knee"
                    ),
                )
            )
        )
        assert recent.risk_score > old.risk_score

    def test_recurrent_soft_tissue_outweighs_varied_injuries(self) -> None:
        recurrent = score_health(
            inputs(
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=3,
                        region=BodyRegion.HAMSTRING,
                        raw_descriptor="Hamstring",
                    ),
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=12,
                        region=BodyRegion.HAMSTRING,
                        raw_descriptor="Hamstring",
                    ),
                )
            )
        )
        varied = score_health(
            inputs(
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=3,
                        region=BodyRegion.HAND_WRIST_FINGER,
                        raw_descriptor="Thumb",
                    ),
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=12,
                        region=BodyRegion.SHOULDER,
                        raw_descriptor="Shoulder",
                    ),
                )
            )
        )
        assert recurrent.risk_score > varied.risk_score
        assert any(c.name == "recurrent_injury" for c in recurrent.components)

    def test_one_long_absence_is_not_scored_as_a_recurring_problem(self) -> None:
        """The board's original fault, stated as a test.

        A quarterback listed OUT with the same elbow every week from week 10 to
        the end of the season generated nine rows, which the model read as nine
        elbow injuries and charged the full recurrence penalty for. One injury
        cannot be a pattern, however long it lasted.
        """
        assessment = score_health(
            inputs(
                position=Position.QB,
                injury_history=tuple(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=week,
                        region=BodyRegion.ARM_ELBOW,
                        raw_descriptor="Elbow",
                        designation=InjuryDesignation.OUT,
                    )
                    for week in range(10, 19)
                ),
            )
        )
        assert not any(c.name == "recurrent_injury" for c in assessment.components)
        assert assessment.risk_band == "LOW"

    def test_an_identical_absence_costs_less_when_the_area_heals(self) -> None:
        """Isolates the only judgement the region can support.

        Same player, same weeks, same OUT designations, one injury each: the
        sole difference is that an elbow heals and a hamstring keeps failing.
        Anything that makes these two score equally has thrown that away.
        """
        weeks = range(8, 14)

        def out_all_year(region: BodyRegion) -> float:
            return score_health(
                inputs(
                    injury_history=tuple(
                        InjuryHistoryEvent(
                            season=SEASON - 1,
                            week=week,
                            region=region,
                            raw_descriptor=region.value,
                            designation=InjuryDesignation.OUT,
                        )
                        for week in weeks
                    )
                )
            ).risk_score

        assert out_all_year(BodyRegion.ARM_ELBOW) < out_all_year(BodyRegion.HAMSTRING)

    def test_time_lost_still_registers_after_the_discount(self) -> None:
        """Discounting a freak injury must not make it disappear.

        Missing half a season is a fact about the player's last year even when
        the area that caused it is not expected to fail again.
        """
        missed_half_a_season = score_health(
            inputs(
                injury_history=tuple(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=week,
                        region=BodyRegion.ARM_ELBOW,
                        raw_descriptor="Elbow",
                        designation=InjuryDesignation.OUT,
                    )
                    for week in range(10, 19)
                )
            )
        )
        played_through = score_health(
            inputs(
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=10,
                        region=BodyRegion.ARM_ELBOW,
                        raw_descriptor="Elbow",
                        designation=InjuryDesignation.QUESTIONABLE,
                    ),
                )
            )
        )
        assert missed_half_a_season.risk_score > played_through.risk_score
        assert any(c.name == "games_missed" for c in missed_half_a_season.components)

    def test_repeated_impact_injuries_are_noted_but_not_punished_as_a_pattern(
        self,
    ) -> None:
        """Two broken hands is bad luck twice; two pulled calves is a body."""
        hands = score_health(
            inputs(
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=3,
                        region=BodyRegion.HAND_WRIST_FINGER,
                        raw_descriptor="Hand",
                    ),
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        week=14,
                        region=BodyRegion.HAND_WRIST_FINGER,
                        raw_descriptor="Hand",
                    ),
                )
            )
        )
        calves = score_health(
            inputs(
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 1, week=3, region=BodyRegion.CALF, raw_descriptor="Calf"
                    ),
                    InjuryHistoryEvent(
                        season=SEASON - 1, week=14, region=BodyRegion.CALF, raw_descriptor="Calf"
                    ),
                )
            )
        )
        assert any(c.name == "recurrent_injury" for c in hands.components)
        assert hands.risk_score < calves.risk_score

    def test_rest_days_are_not_treated_as_injuries(self) -> None:
        """'Not injury related - resting player' must not accrue injury burden."""
        rested = score_health(
            inputs(
                injury_history=tuple(
                    InjuryHistoryEvent(
                        season=SEASON - 1,
                        region=BodyRegion.REST,
                        raw_descriptor="Not injury related - resting player",
                    )
                    for _ in range(5)
                )
            )
        )
        assert not any(c.name == "injury_burden" for c in rested.components)
        assert rested.risk_score == 0.0


class TestAgeCurve:
    def test_running_backs_age_earlier_than_receivers(self) -> None:
        rb = score_health(inputs(position=Position.RB, age=30.0))
        wr = score_health(inputs(position=Position.WR, age=30.0))
        assert rb.risk_score > wr.risk_score

    def test_a_young_player_gets_no_age_penalty(self) -> None:
        assessment = score_health(inputs(position=Position.RB, age=23.0))
        assert not any(c.name == "age_curve" for c in assessment.components)

    def test_quarterbacks_age_latest(self) -> None:
        qb = score_health(inputs(position=Position.QB, age=33.0))
        assert not any(c.name == "age_curve" for c in qb.components)


class TestWorkload:
    def test_a_full_season_earns_a_durability_discount(self) -> None:
        assessment = score_health(inputs(workload=WorkloadSummary(season=2025, games_played=17)))
        durability = [c for c in assessment.components if c.name == "demonstrated_durability"]
        assert durability and durability[0].points < 0

    def test_heavy_usage_adds_exposure_risk(self) -> None:
        assessment = score_health(
            inputs(
                position=Position.RB,
                workload=WorkloadSummary(
                    season=2025, games_played=12, carries_per_game=22.0, targets_per_game=4.0
                ),
            )
        )
        assert any(c.name == "workload_exposure" for c in assessment.components)


class TestConfidenceAndHonesty:
    def test_missing_data_lowers_confidence_rather_than_inventing_risk(self) -> None:
        """An unmeasured player is unknown, not safe - and the score says so."""
        sparse = score_health(inputs())
        assert sparse.risk_score == 0.0
        assert sparse.confidence < 0.5
        assert any("provisional" in limitation for limitation in sparse.limitations)

    def test_complete_data_yields_high_confidence(self) -> None:
        assessment = score_health(
            inputs(
                designation=InjuryDesignation.QUESTIONABLE,
                age=27.0,
                practice_statuses=(PracticeStatus.FULL, PracticeStatus.FULL),
                injury_history=(
                    InjuryHistoryEvent(
                        season=SEASON - 1, region=BodyRegion.ANKLE, raw_descriptor="Ankle"
                    ),
                ),
                workload=WorkloadSummary(season=2025, games_played=16),
            )
        )
        assert assessment.confidence == 1.0

    def test_limitations_are_always_present(self) -> None:
        assessment = score_health(inputs())
        assert assessment.limitations
        assert any("not medical" in x.lower() for x in assessment.limitations)

    def test_availability_falls_as_risk_rises(self) -> None:
        low = score_health(inputs(designation=InjuryDesignation.ACTIVE))
        high = score_health(inputs(designation=InjuryDesignation.IR))
        assert low.availability_estimate > high.availability_estimate
        assert 0.0 <= high.availability_estimate <= 1.0

    def test_model_version_is_recorded(self) -> None:
        assert score_health(inputs()).model_version == "heuristic-v2"
