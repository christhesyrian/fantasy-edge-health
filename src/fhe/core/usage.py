"""Measured opportunity, and how steady a player's scoring was.

Why this exists separately from the projection
----------------------------------------------
A projection already encodes both talent and expected volume, so anything that
re-derives value from the same stats would count it twice. These two signals are
deliberately *not* that:

* **Opportunity** is evidence about whether a projection is well founded. A
  player projected for a big season on 20% of his team's snaps is a bet; the
  same projection on 85% is corroborated. Used only to *discount* the
  unsupported case — never to reward the supported one, because the reward is
  already in the projection.

* **Consistency** is the week-to-week spread of what a player actually scored.
  It is orthogonal to a season total: two players projected for 250 points can
  arrive there very differently, and which you want depends on your roster, not
  on which is better. So it informs the "safest" and "highest upside" reads
  rather than reordering the board.

Both are absent for anyone without a measured season — rookies most obviously.
Absent means *unknown*, which lowers confidence and applies no adjustment. It
must never be read as "low", which would invent a risk from missing data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Below this many games a season's averages describe a fluke rather than a role.
MIN_GAMES_FOR_USAGE: Final = 4

# The snap share at which a projection is considered corroborated — **per
# position**, because the same number means completely different things. A back
# splitting carries at 55% is playing a normal committee role; a receiver at 55%
# is a part-timer, and a quarterback at 55% is a backup. One global threshold
# read every receiver and tight end as fully corroborated and made the signal
# almost inert.
#
# Set near the low end of what a full-time player at each position actually
# runs, so the discount catches genuine part-timers rather than penalising
# ordinary rotation.
CORROBORATING_SNAP_SHARE: Final[dict[str, float]] = {
    "QB": 0.85,
    "RB": 0.55,
    "WR": 0.80,
    "TE": 0.75,
}

# Positions whose snap share carries no comparable meaning, so no judgement is
# made from it. A kicker plays a handful of snaps by design.
UNJUDGED_POSITIONS: Final[frozenset[str]] = frozenset({"K", "DEF"})

# Games in a season, for turning a season projection into a per-game rate.
GAMES_IN_SEASON: Final = 17

# Below this scoring rate a coefficient of variation stops meaning anything: a
# player averaging 0.04 points a game shows a "volatility" of 3.5 purely
# because the denominator is nearly zero. That is arithmetic, not boom-or-bust,
# so no volatility is reported at all rather than a number that would rank a
# fourth-string back among the highest-upside picks on the board.
MIN_POINTS_FOR_VOLATILITY: Final = 3.0


@dataclass(frozen=True, slots=True)
class UsageProfile:
    """What a player's most recent measured season actually looked like.

    Args:
        season: Season these figures describe.
        games_sampled: Games with a recorded stat line.
        snap_share: Mean share of offensive snaps, 0-1.
        touches_per_game: Carries plus targets.
        points_per_game: Mean fantasy points in the league's scoring format.
        points_stdev: Week-to-week standard deviation of those points.
    """

    season: int | None = None
    games_sampled: int | None = None
    snap_share: float | None = None
    touches_per_game: float | None = None
    points_per_game: float | None = None
    points_stdev: float | None = None

    @property
    def is_measured(self) -> bool:
        """Whether there is enough of a sample to say anything at all."""
        return (self.games_sampled or 0) >= MIN_GAMES_FOR_USAGE

    def opportunity_support(self, position: str) -> float | None:
        """How far measured opportunity corroborates a projection, 0-1.

        ``None`` when snap share was never measured, or when the position's
        snap share carries no comparable meaning. Absent is different from zero:
        an unmeasured player is unknown, not idle.
        """
        if not self.is_measured or self.snap_share is None:
            return None
        if position in UNJUDGED_POSITIONS:
            return None
        threshold = CORROBORATING_SNAP_SHARE.get(position)
        if threshold is None:
            return None
        return min(1.0, self.snap_share / threshold)

    def production_support(self, projected_points: float | None) -> float | None:
        """How far last season's scoring already justifies the projection, 0-1.

        The other half of corroboration, and the half that stops the snap-share
        test misreading efficiency as risk. A receiver on 70% of snaps who
        nonetheless outscored the projection being asked of him is not resting
        on a step up — he has already done it. Judging him on opportunity alone
        flagged exactly that player.
        """
        if not self.is_measured or self.points_per_game is None:
            return None
        if projected_points is None or projected_points <= 0:
            return None
        projected_per_game = projected_points / GAMES_IN_SEASON
        if projected_per_game <= 0:
            return None
        return min(1.0, self.points_per_game / projected_per_game)

    def corroboration(self, position: str, projected_points: float | None) -> float | None:
        """The best evidence available that a projection is well founded, 0-1.

        Opportunity **or** production: either is enough. Requiring both would
        penalise the efficient and the heavily-used alike, and the question here
        is only whether *some* measurement supports the projection.
        """
        signals = [
            value
            for value in (
                self.opportunity_support(position),
                self.production_support(projected_points),
            )
            if value is not None
        ]
        return max(signals) if signals else None

    @property
    def volatility(self) -> float | None:
        """Week-to-week spread relative to the mean — a coefficient of variation.

        Relative rather than absolute so a scoring leader and a bench player are
        comparable: four points of swing means something very different at 20
        points a game than at six.
        """
        if not self.is_measured or self.points_stdev is None:
            return None
        if self.points_per_game is None or self.points_per_game < MIN_POINTS_FOR_VOLATILITY:
            return None
        return self.points_stdev / self.points_per_game
