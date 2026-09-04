"""How willing a team is to give rookies the ball, under its current coach.

The question this answers
-------------------------
A rookie has no measured usage, no injury history worth reading, and a
projection built on college tape and draft position rather than anything the
NFL has seen. Almost every signal this engine trusts is blank for them.

What *is* knowable is the situation they landed in. Coaching staffs differ
sharply and persistently in whether they play rookies, and that is a property of
the staff rather than the franchise — which is the whole reason tenure matters
here. A team that fed rookies under its last coach tells you nothing about the
one who replaced him.

Two signals, both about the landing spot:

* **A recent precedent.** Did this team give a rookie a real workload last
  season? That is the most direct evidence available that the door is open.
* **A pattern.** Across every season under the *current* coach, how many
  offensive touches went to rookies on average? Teams are ranked on that, and
  the boost falls away down the order.

Deliberately about opportunity, not talent. Nothing here says a player is good;
it says the situation has historically allowed rookies to produce. A rookie on a
team with no history under a brand-new coach gets **nothing** — not a penalty,
because an unknown situation is unknown, and this model must not invent a
verdict about a coach who has not coached yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# A rookie workload that counts as a real role rather than mop-up duty.
MEANINGFUL_ROOKIE_TOUCHES: Final = 50

# Where the boost stops. Teams ranked outside this get nothing rather than a
# negative: a team that has not used rookies is not evidence against a
# particular rookie, only an absence of evidence for him.
RANKED_TEAMS: Final = 30

# The boost for the most rookie-friendly staff, in ranking points, before it
# decays down the order. Small on purpose — this is a tiebreaker among rookies
# whose projections are already uncertain, not a claim about talent.
TOP_TEAM_BOOST: Final = 6.0

# Added on top when the team gave a rookie a real workload last season, which is
# the most direct precedent available.
RECENT_PRECEDENT_BOOST: Final = 2.5


@dataclass(frozen=True, slots=True)
class RookieOpportunity:
    """What a team's recent history says about rookies getting the ball.

    Args:
        team: Team code this describes.
        coach: The current head coach, whose tenure bounds every figure here.
        seasons_under_coach: Seasons of measured history under that coach.
        average_rookie_touches: Mean offensive touches by rookies per season,
            across that tenure.
        rank: Position among ranked teams, 1 being the most rookie-friendly.
        teams_ranked: How many teams had enough history to rank at all.
        had_recent_workhorse: Whether a rookie reached a meaningful workload in
            the most recent season under this coach.
    """

    team: str
    coach: str | None = None
    seasons_under_coach: int = 0
    average_rookie_touches: float | None = None
    rank: int | None = None
    teams_ranked: int = 0
    had_recent_workhorse: bool = False

    @property
    def is_measured(self) -> bool:
        """Whether the current coach has any history to read."""
        return self.seasons_under_coach > 0 and self.average_rookie_touches is not None

    @property
    def boost(self) -> float:
        """Ranking points to add for a rookie landing here.

        Zero for a staff with no history — a new coach is an unknown, and
        guessing either way would be inventing a verdict about someone who has
        not coached the team yet.
        """
        if not self.is_measured or self.rank is None:
            return 0.0
        if self.rank > RANKED_TEAMS:
            return 0.0
        # Linear decay from the top of the order downward, so the difference
        # between first and second is the same as between twentieth and
        # twenty-first. A steeper curve would imply a precision this
        # measurement does not have.
        #
        # Divided by the range rather than one less than it, so the last ranked
        # team still receives a small boost. Decaying to exactly zero at the
        # bottom would make the thirtieth team indistinguishable from an
        # unranked one, and those are different statements: a little evidence
        # is not the same as none.
        decayed = TOP_TEAM_BOOST * (1.0 - (self.rank - 1) / RANKED_TEAMS)
        precedent = RECENT_PRECEDENT_BOOST if self.had_recent_workhorse else 0.0
        return round(decayed + precedent, 2)
