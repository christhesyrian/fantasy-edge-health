"""Tests for the injury taxonomy.

The headline test is :func:`test_coverage_against_real_vocabulary`, which runs
the normaliser over every injury descriptor actually observed in seven seasons
of nflverse data and asserts a hard coverage floor. That is what stops a
refactor from silently degrading the mapping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fhe.core.injury import (
    normalize_body_region,
    normalize_body_regions,
    normalize_designation,
    normalize_practice_status,
    practice_trajectory,
)
from fhe.core.types import (
    BodyRegion,
    InjuryDesignation,
    PracticeStatus,
    PracticeTrajectory,
)

FIXTURE = Path(__file__).parents[2] / "data" / "fixtures" / "nflverse_injury_descriptors.json"

# Empirically achieved coverage is 99.96% of observations. The floor is set just
# below that so an accidental regression fails, while genuinely unmappable
# descriptors ("Cramps", "Other", "--") are tolerated.
MIN_COVERAGE = 0.999


pytestmark = pytest.mark.unit


class TestBodyRegionNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # laterality is noise, not information
            ("right Shoulder", BodyRegion.SHOULDER),
            ("Left Knee", BodyRegion.KNEE),
            ("Right Wrist", BodyRegion.HAND_WRIST_FINGER),
            # specific beats generic
            ("Achilles", BodyRegion.ACHILLES),
            ("Heel", BodyRegion.FOOT_TOE),
            ("Lower Leg", BodyRegion.CALF),
            # compound diagnoses collapse to the region
            ("Knee - ACL", BodyRegion.KNEE),
            ("Knee - ACL + MCL", BodyRegion.KNEE),
            ("Knee - Meniscus", BodyRegion.KNEE),
            # plurals
            ("Ankles", BodyRegion.ANKLE),
            ("Hips", BodyRegion.HIP_GROIN),
            ("calves", BodyRegion.CALF),
            # organs sit in the torso bucket
            ("kidney", BodyRegion.TORSO_RIBS),
            ("Appendicitis", BodyRegion.TORSO_RIBS),
            # nerve injuries
            ("Stinger", BodyRegion.NECK),
            ("Burner", BodyRegion.NECK),
            # explicit non-disclosure
            ("Undisclosed", BodyRegion.UNDISCLOSED),
            ("Lower Body", BodyRegion.UNDISCLOSED),
        ],
    )
    def test_maps_observed_descriptors(self, raw: str, expected: BodyRegion) -> None:
        assert normalize_body_region(raw) is expected

    def test_chest_is_not_mistaken_for_rest(self) -> None:
        """Regression: substring matching would classify 'chest' as a rest day."""
        assert normalize_body_region("Chest") is BodyRegion.TORSO_RIBS
        assert normalize_body_region("Rest") is BodyRegion.REST

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Not injury related - resting player", BodyRegion.REST),
            ("Not injury related - coach's decision", BodyRegion.REST),
            ("Coaching Decision ", BodyRegion.REST),
            ("Not injury related - personal matter", BodyRegion.NON_INJURY),
            ("not injury related - returning from suspension", BodyRegion.NON_INJURY),
            ("Non-Football Injury", BodyRegion.NON_INJURY),
            ("NIR-medical", BodyRegion.NON_INJURY),
            ("Jury Duty", BodyRegion.NON_INJURY),
        ],
    )
    def test_non_injury_reasons_beat_body_parts(self, raw: str, expected: BodyRegion) -> None:
        """'Not injury related - resting player' is a rest day, not a leg injury."""
        assert normalize_body_region(raw) is expected

    def test_multi_part_descriptor_returns_every_region_in_order(self) -> None:
        assert normalize_body_regions("back, ankle, knee") == (
            BodyRegion.BACK,
            BodyRegion.ANKLE,
            BodyRegion.KNEE,
        )

    def test_absent_text_is_distinguishable_from_unrecognised_text(self) -> None:
        assert normalize_body_regions(None) == ()
        assert normalize_body_regions("   ") == ()
        assert normalize_body_regions("zzzz") == (BodyRegion.OTHER_UNKNOWN,)
        # the convenience wrapper always yields a usable member
        assert normalize_body_region(None) is BodyRegion.OTHER_UNKNOWN

    def test_soft_tissue_classification(self) -> None:
        assert BodyRegion.HAMSTRING.is_soft_tissue
        assert BodyRegion.CALF.is_soft_tissue
        assert BodyRegion.HIP_GROIN.is_soft_tissue
        assert not BodyRegion.KNEE.is_soft_tissue
        assert not BodyRegion.HAND_WRIST_FINGER.is_soft_tissue

    def test_coverage_against_real_vocabulary(self) -> None:
        """Every descriptor seen in 2019-2025 nflverse data, weighted by frequency."""
        payload = json.loads(FIXTURE.read_text())
        total = 0
        unmapped = 0
        for entry in payload["descriptors"]:
            total += entry["count"]
            if normalize_body_region(entry["value"]) is BodyRegion.OTHER_UNKNOWN:
                unmapped += entry["count"]

        coverage = (total - unmapped) / total
        assert coverage >= MIN_COVERAGE, (
            f"taxonomy coverage regressed to {coverage:.4%} "
            f"({unmapped}/{total} observations unmapped)"
        )


class TestPracticeStatus:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Did Not Participate In Practice", PracticeStatus.DNP),
            ("Limited Participation in Practice", PracticeStatus.LIMITED),
            ("Full Participation in Practice", PracticeStatus.FULL),
            ("Note", PracticeStatus.UNKNOWN),
            (None, PracticeStatus.UNKNOWN),
        ],
    )
    def test_normalisation(self, raw: str | None, expected: PracticeStatus) -> None:
        assert normalize_practice_status(raw) is expected

    def test_whitespace_padding_rows_are_unknown_not_full(self) -> None:
        """nflverse ships literal '\\n    ' padding; it must not read as a report."""
        assert normalize_practice_status("\n    ") is PracticeStatus.UNKNOWN
        assert normalize_practice_status("") is PracticeStatus.UNKNOWN


class TestPracticeTrajectory:
    def test_improving(self) -> None:
        assert (
            practice_trajectory([PracticeStatus.DNP, PracticeStatus.LIMITED, PracticeStatus.FULL])
            is PracticeTrajectory.IMPROVING
        )

    def test_worsening(self) -> None:
        assert (
            practice_trajectory([PracticeStatus.FULL, PracticeStatus.LIMITED, PracticeStatus.DNP])
            is PracticeTrajectory.WORSENING
        )

    def test_stable(self) -> None:
        assert (
            practice_trajectory([PracticeStatus.FULL, PracticeStatus.FULL])
            is PracticeTrajectory.STABLE
        )

    def test_unknown_entries_are_dropped_not_treated_as_middle_ground(self) -> None:
        assert (
            practice_trajectory([PracticeStatus.UNKNOWN, PracticeStatus.FULL])
            is PracticeTrajectory.INSUFFICIENT_DATA
        )
        assert (
            practice_trajectory([PracticeStatus.DNP, PracticeStatus.UNKNOWN, PracticeStatus.FULL])
            is PracticeTrajectory.IMPROVING
        )

    def test_single_report_is_insufficient(self) -> None:
        assert practice_trajectory([PracticeStatus.FULL]) is PracticeTrajectory.INSUFFICIENT_DATA
        assert practice_trajectory([]) is PracticeTrajectory.INSUFFICIENT_DATA


class TestDesignation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Questionable", InjuryDesignation.QUESTIONABLE),
            ("Doubtful", InjuryDesignation.DOUBTFUL),
            ("Out", InjuryDesignation.OUT),
            ("IR", InjuryDesignation.IR),
            ("PUP", InjuryDesignation.PUP),
            ("Sus", InjuryDesignation.SUSPENDED),
            ("COV", InjuryDesignation.COVID),
            ("DNR", InjuryDesignation.DID_NOT_REPORT),
            ("NA", InjuryDesignation.NOT_ACTIVE),
            ("Note", InjuryDesignation.UNKNOWN),
        ],
    )
    def test_sleeper_and_nflverse_values(self, raw: str, expected: InjuryDesignation) -> None:
        assert normalize_designation(raw) is expected

    def test_absent_status_means_no_designation_on_file(self) -> None:
        """Absence of a report is not evidence of health, but it is not a flag."""
        assert normalize_designation(None) is InjuryDesignation.ACTIVE
        assert normalize_designation("") is InjuryDesignation.ACTIVE

    def test_unrecognised_status_is_recorded_not_guessed(self) -> None:
        assert normalize_designation("Probable-ish") is InjuryDesignation.UNKNOWN

    @pytest.mark.parametrize(
        ("designation", "rules_out"),
        [
            (InjuryDesignation.OUT, True),
            (InjuryDesignation.IR, True),
            (InjuryDesignation.PUP, True),
            (InjuryDesignation.SUSPENDED, True),
            (InjuryDesignation.QUESTIONABLE, False),
            (InjuryDesignation.DOUBTFUL, False),
            (InjuryDesignation.ACTIVE, False),
        ],
    )
    def test_rules_out_the_week(self, designation: InjuryDesignation, rules_out: bool) -> None:
        assert designation.rules_out_the_week is rules_out
