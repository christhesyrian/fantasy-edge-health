"""Tests for collapsing weekly injury reports into distinct injuries.

The archive publishes one row per weekly injury report, so a single long absence
arrives as many rows. Every test here exists because scoring those rows as
separate injuries produced a specific wrong answer on the real board.
"""

from __future__ import annotations

import pytest

from fhe.core.health.models import InjuryHistoryEvent
from fhe.core.health.spells import collapse_to_spells
from fhe.core.types import BodyRegion, InjuryDesignation

pytestmark = pytest.mark.unit


def event(
    season: int,
    week: int | None,
    region: BodyRegion,
    designation: InjuryDesignation = InjuryDesignation.QUESTIONABLE,
    games_missed: int | None = None,
) -> InjuryHistoryEvent:
    """Build one weekly injury report."""
    return InjuryHistoryEvent(
        season=season,
        week=week,
        region=region,
        raw_descriptor=region.value.title(),
        designation=designation,
        games_missed=games_missed,
    )


class TestCollapsing:
    def test_a_long_absence_is_one_injury(self) -> None:
        """The case that motivated the module.

        A quarterback dislocates an elbow in week 10 and is listed OUT every
        week to the end of the season. That is one injury, not eight.
        """
        spells = collapse_to_spells(
            tuple(
                event(2025, week, BodyRegion.ARM_ELBOW, InjuryDesignation.OUT)
                for week in (10, 11, 12, 13, 14, 15, 16, 17, 18)
            )
        )
        assert len(spells) == 1
        assert spells[0].reports == 9
        assert spells[0].first_week == 10
        assert spells[0].last_week == 18

    def test_a_gap_within_tolerance_stays_one_injury(self) -> None:
        """A missing week is a bye or a full practice, not a second injury."""
        spells = collapse_to_spells(
            (
                event(2025, 10, BodyRegion.KNEE),
                event(2025, 12, BodyRegion.KNEE),
            )
        )
        assert len(spells) == 1

    def test_injuries_months_apart_stay_separate(self) -> None:
        """September and December hamstrings are the pattern the model wants."""
        spells = collapse_to_spells(
            (
                event(2025, 2, BodyRegion.HAMSTRING),
                event(2025, 14, BodyRegion.HAMSTRING),
            )
        )
        assert len(spells) == 2

    def test_different_regions_never_merge(self) -> None:
        spells = collapse_to_spells(
            (
                event(2025, 6, BodyRegion.ANKLE),
                event(2025, 7, BodyRegion.SHOULDER),
            )
        )
        assert len(spells) == 2

    def test_an_injury_bridging_the_new_year_is_one_injury(self) -> None:
        """Week 18 and the following week 1 are the same knee."""
        spells = collapse_to_spells(
            (
                event(2025, 18, BodyRegion.KNEE, InjuryDesignation.OUT),
                event(2026, 1, BodyRegion.KNEE, InjuryDesignation.OUT),
            )
        )
        assert len(spells) == 1
        assert spells[0].first_season == 2025
        # Recency is measured from the later season: it says when the player was
        # last actually unavailable.
        assert spells[0].last_season == 2026

    def test_the_same_region_in_consecutive_midseasons_stays_separate(self) -> None:
        spells = collapse_to_spells(
            (
                event(2025, 5, BodyRegion.KNEE),
                event(2026, 9, BodyRegion.KNEE),
            )
        )
        assert len(spells) == 2

    def test_undated_reports_do_not_invent_extra_injuries(self) -> None:
        """Without week numbers there is no evidence of a second injury."""
        spells = collapse_to_spells(tuple(event(2025, None, BodyRegion.CALF) for _ in range(4)))
        assert len(spells) == 1
        assert spells[0].reports == 4

    def test_reports_may_arrive_in_any_order(self) -> None:
        spells = collapse_to_spells(
            (
                event(2025, 13, BodyRegion.ANKLE),
                event(2025, 11, BodyRegion.ANKLE),
                event(2025, 12, BodyRegion.ANKLE),
            )
        )
        assert len(spells) == 1
        assert (spells[0].first_week, spells[0].last_week) == (11, 13)

    def test_no_reports_yields_no_spells(self) -> None:
        assert collapse_to_spells(()) == ()


class TestSpellFacts:
    def test_absence_counts_only_designations_that_mean_absent(self) -> None:
        spell = collapse_to_spells(
            (
                event(2025, 5, BodyRegion.KNEE, InjuryDesignation.OUT),
                event(2025, 6, BodyRegion.KNEE, InjuryDesignation.OUT),
                event(2025, 7, BodyRegion.KNEE, InjuryDesignation.QUESTIONABLE),
            )
        )[0]
        assert spell.weeks_absent == 2
        assert spell.missed_weeks == 2

    def test_a_provider_games_missed_count_wins_over_the_fallback(self) -> None:
        spell = collapse_to_spells(
            (event(2025, 5, BodyRegion.KNEE, InjuryDesignation.OUT, games_missed=6),)
        )[0]
        assert spell.missed_weeks == 6

    def test_the_worst_designation_describes_the_spell(self) -> None:
        spell = collapse_to_spells(
            (
                event(2025, 5, BodyRegion.KNEE, InjuryDesignation.QUESTIONABLE),
                event(2025, 6, BodyRegion.KNEE, InjuryDesignation.IR),
                event(2025, 7, BodyRegion.KNEE, InjuryDesignation.DOUBTFUL),
            )
        )[0]
        assert spell.worst_designation is InjuryDesignation.IR

    def test_raw_provider_text_is_never_discarded(self) -> None:
        spells = collapse_to_spells(
            (
                InjuryHistoryEvent(
                    season=2025, week=5, region=BodyRegion.KNEE, raw_descriptor="Knee - ACL"
                ),
                InjuryHistoryEvent(
                    season=2025, week=6, region=BodyRegion.KNEE, raw_descriptor="Left Knee"
                ),
            )
        )
        assert spells[0].raw_descriptors == ("Knee - ACL", "Left Knee")
