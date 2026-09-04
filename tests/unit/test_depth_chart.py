"""Tests for reading a depth-chart listing as evidence for a projection."""

from __future__ import annotations

import pytest

from fhe.core.depth import DepthChartPlacement

pytestmark = pytest.mark.unit


def placement(position: str, rank: int) -> DepthChartPlacement:
    """A listing at one position and rank."""
    return DepthChartPlacement(team="SEA", position=position, rank=rank)


class TestStarter:
    @pytest.mark.parametrize(
        ("position", "rank", "expected"),
        [
            ("QB", 1, True),
            ("QB", 2, False),
            ("RB", 1, True),
            ("RB", 2, False),
            # Three receivers start in the modern base offence, which is why
            # the provider's own formation grouping is called "3WR 1TE".
            ("WR", 3, True),
            ("WR", 4, False),
            ("TE", 1, True),
            ("TE", 2, False),
        ],
    )
    def test_starting_depends_on_how_many_play_at_that_position(
        self, position: str, rank: int, expected: bool
    ) -> None:
        assert placement(position, rank).is_starter is expected

    def test_the_label_reads_the_way_the_position_is_spoken(self) -> None:
        assert placement("RB", 2).label == "RB2"


class TestRoleSupport:
    def test_a_starter_fully_supports_a_projection(self) -> None:
        assert placement("RB", 1).role_support() == 1.0

    def test_support_falls_with_depth(self) -> None:
        ladder = [placement("WR", rank).role_support() for rank in (1, 2, 3, 4)]
        assert ladder == sorted(ladder, reverse=True)
        assert len(set(ladder)) == len(ladder)

    def test_a_backup_quarterback_supports_almost_nothing(self) -> None:
        """His projection rests on someone else's injury."""
        assert placement("QB", 2).role_support() < placement("RB", 2).role_support()

    def test_a_second_running_back_still_counts_for_something(self) -> None:
        """Committees are the norm, so an RB2 has a real role."""
        assert placement("RB", 2).role_support() > placement("TE", 2).role_support()

    def test_a_player_listed_beyond_the_ladder_is_not_zeroed(self) -> None:
        """A deep listing fails to support a projection; it does not disprove it.

        Charts go stale and injuries promote people, and the current file has
        Green Bay's lead back at RB4.
        """
        assert placement("RB", 9).role_support() > 0.0

    def test_an_unranked_listing_is_treated_as_buried(self) -> None:
        assert placement("WR", 0).role_support() == placement("WR", 40).role_support()

    def test_a_position_with_no_ladder_supports_nothing_much(self) -> None:
        """A kicker has a depth chart and no use for one here."""
        assert placement("K", 1).role_support() < 0.5
