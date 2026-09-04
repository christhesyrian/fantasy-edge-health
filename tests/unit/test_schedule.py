"""Fantasy-playoff strength of schedule."""

from __future__ import annotations

import pytest

from fhe.core.schedule import MIN_MATCHUPS, PlayoffSchedule

pytestmark = pytest.mark.unit


def schedule(**overrides: object) -> PlayoffSchedule:
    base: dict[str, object] = {
        "weeks_covered": 3,
        "opponents": ("CIN", "PIT", "SEA"),
        "points_allowed_per_game": 8.25,
        "league_average": 7.44,
    }
    base.update(overrides)
    return PlayoffSchedule(**base)  # type: ignore[arg-type]


class TestMeasurement:
    def test_too_few_matchups_is_not_a_schedule(self) -> None:
        assert not schedule(weeks_covered=MIN_MATCHUPS - 1).is_measured

    def test_nothing_measured_reports_unknown(self) -> None:
        """Absent must not read as an average draw."""
        assert PlayoffSchedule().difficulty is None

    def test_a_zero_league_average_is_refused(self) -> None:
        """Guards a division by zero on a position nobody scored against."""
        assert schedule(league_average=0.0).difficulty is None


class TestDifficulty:
    def test_above_one_is_a_favourable_draw(self) -> None:
        """More points allowed than average means an easier matchup."""
        assert schedule().difficulty == pytest.approx(8.25 / 7.44)
        assert schedule().difficulty is not None
        assert schedule().difficulty > 1.0  # type: ignore[operator]

    def test_below_one_is_a_hard_draw(self) -> None:
        hard = schedule(points_allowed_per_game=6.5)

        assert hard.difficulty is not None
        assert hard.difficulty < 1.0

    def test_it_is_a_ratio_rather_than_a_rank(self) -> None:
        """Two defences can be many rank places and a fraction of a point apart.

        A ratio keeps the size of the difference, which is what decides whether
        the signal deserves any weight at all.
        """
        barely_easier = schedule(points_allowed_per_game=7.5, league_average=7.44)

        assert barely_easier.difficulty is not None
        assert 1.0 < barely_easier.difficulty < 1.02
