"""Strength of schedule for the weeks a fantasy season is decided in.

Why the playoff weeks specifically
----------------------------------
A season projection already accounts for playing seventeen games against an
average opponent, so season-long strength of schedule is close to noise: over a
full year the hard and soft matchups mostly cancel, and whatever is left is
already inside the projection. Adding it would be a rounding error dressed as a
signal.

The fantasy playoffs are different. Most leagues decide everything in weeks
15-17, a projection weights those weeks no differently from week 3, and three
games is a small enough sample that the schedule genuinely does not average out.
That is a fact about the season a projection cannot contain, which is what makes
it worth scoring.

What "strength" means here
--------------------------
Fantasy points a defence allowed to a position, measured from this system's own
weekly stats. Not points allowed overall — a defence can be stingy against the
run and generous to receivers, and a running back cares only about the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# The weeks most leagues play their championship in. Configurable per league is
# the obvious refinement, but this is the near-universal default and guessing
# wrong by a week barely changes a three-game average.
PLAYOFF_WEEKS: Final[tuple[int, ...]] = (15, 16, 17)

# Below this many measured matchups the average describes noise rather than a
# schedule, so no rating is produced at all.
MIN_MATCHUPS: Final = 2


@dataclass(frozen=True, slots=True)
class PlayoffSchedule:
    """How hard a player's fantasy-playoff matchups look.

    Args:
        weeks_covered: How many of the playoff weeks had a known opponent.
        opponents: Opponent team codes, in week order.
        points_allowed_per_game: Mean fantasy points those defences allowed to
            this player's position, across the sampled season.
        league_average: The same measure across every defence, for comparison.
    """

    weeks_covered: int = 0
    opponents: tuple[str, ...] = ()
    points_allowed_per_game: float | None = None
    league_average: float | None = None

    @property
    def is_measured(self) -> bool:
        """Whether there is enough here to say anything."""
        return (
            self.weeks_covered >= MIN_MATCHUPS
            and self.points_allowed_per_game is not None
            and self.league_average is not None
            and self.league_average > 0
        )

    @property
    def difficulty(self) -> float | None:
        """Matchup quality as a ratio to the league average.

        Above 1.0 means the defences faced gave up *more* than average to this
        position — a favourable draw. Below 1.0 is a hard one. Expressed as a
        ratio rather than a rank so the size of the difference survives: two
        teams can be a dozen rank places apart and a fraction of a point apart.
        """
        if not self.is_measured:
            return None
        assert self.points_allowed_per_game is not None  # narrowed by is_measured
        assert self.league_average is not None
        return self.points_allowed_per_game / self.league_average
