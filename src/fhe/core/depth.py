"""Where a player sits on his team's depth chart, and what that is worth.

The problem this solves
-----------------------
Opportunity corroboration (:mod:`fhe.core.usage`) asks whether last season's
measured snaps and production justify this season's projection. That question is
unanswerable for exactly the players a draft turns on: a rookie has no NFL
snaps, and a receiver who changed teams played his snaps in an offence he has
left. The model's rule is that missing data lowers confidence rather than
inventing risk, so those players simply skipped the check - and a projection
resting on nothing scored the same as one resting on a proven role.

A depth chart is the missing evidence. It is the only *forward*-looking public
statement of a player's role, and it covers the rookie and the new arrival that
last season's usage cannot reach.

What this is deliberately not
-----------------------------
It is not a second bonus. Depth position feeds the corroboration the engine
already computes, as one more way for a projection to be *supported*; it can
lift a player whose usage record is silent, and it never adds value on top of a
projection that was already well founded. Two signals agreeing must not be
worth twice one signal, and a depth chart is far too soft a thing to inflate a
board with: it is somebody's reading of a practice week.

Nor can it ever penalise. Measured usage and the listing are combined by taking
the better of the two, so a wrong chart costs a player nothing. That is not
caution for its own sake - the current charts list Josh Jacobs, Green Bay's
clear lead back, at RB4 behind MarShawn Lloyd. Across the top sixty players by
ADP the source agrees with the market 59 times out of 60, which is good enough
to lean on and nowhere near good enough to demote a first-round back over.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final

# How much a projection is corroborated by standing at each depth rank, by
# position. Index 0 is the starter. These describe how many players at a
# position actually take a meaningful share of an NFL offence's snaps, which is
# a different number for every position and is why one table will not do.
_ROLE_SUPPORT: Final[dict[str, tuple[float, ...]]] = {
    # One quarterback plays. A backup's projection rests on someone else's
    # injury, which is not evidence of anything.
    "QB": (1.0, 0.15),
    # Committees are the norm rather than the exception, so a second back is a
    # real role - but a clear enough step down that it should not fully justify
    # a starter's projection.
    "RB": (1.0, 0.65, 0.25),
    # Three receivers start: the provider's own formation grouping for the
    # modern base offence is literally "3WR 1TE". A fourth is a substitute.
    "WR": (1.0, 0.9, 0.6, 0.25),
    # A second tight end is on the field often and targeted rarely; he is
    # usually there to block.
    "TE": (1.0, 0.35),
}

# Support for a player listed deeper than the table covers. Not zero: charts go
# stale, players are promoted after an injury, and a hard zero would assert that
# a deep listing disproves a projection rather than merely failing to support it.
_BURIED_SUPPORT: Final = 0.1

# Ranks at or above which a player is described as a starter in the UI.
_STARTER_RANK: Final[dict[str, int]] = {"QB": 1, "RB": 1, "WR": 3, "TE": 1}


@dataclass(frozen=True, slots=True)
class DepthChartPlacement:
    """A player's most recent listed position on his team's depth chart.

    Args:
        team: Team the chart belongs to.
        position: Position group the rank is measured within.
        rank: 1 for the starter, counting down the group.
        observed_at: When the provider published this chart. Carried because a
            depth chart in March and one in September are very different claims,
            and the UI must be able to say which it is showing.
    """

    team: str
    position: str
    rank: int
    observed_at: datetime | None = None

    @property
    def is_starter(self) -> bool:
        """Whether this rank is a starting role at this position."""
        return self.rank <= _STARTER_RANK.get(self.position, 1)

    @property
    def label(self) -> str:
        """How the placement reads in the war room, e.g. ``"RB2"``."""
        return f"{self.position}{self.rank}"

    def role_support(self) -> float:
        """How far this listing corroborates a projection, 0-1.

        Deliberately the same shape and scale as
        :meth:`fhe.core.usage.UsageProfile.corroboration`, so the engine can
        take the best evidence available rather than adding the two together.
        """
        ladder = _ROLE_SUPPORT.get(self.position)
        if ladder is None:
            # A kicker or a defence has a depth chart and no use for one here.
            return _BURIED_SUPPORT
        if self.rank < 1:
            return _BURIED_SUPPORT
        if self.rank <= len(ladder):
            return ladder[self.rank - 1]
        return _BURIED_SUPPORT
