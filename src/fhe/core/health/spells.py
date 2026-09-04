"""Collapse weekly injury reports into the injuries that produced them.

An NFL injury report is published several times a week, and a provider archive
records one row per publication. A player who dislocates an elbow in week 10 and
misses the rest of the season therefore appears as seven separate "elbow"
rows - one per week he was still listed.

Counting those rows as seven injuries is wrong twice over, and both errors push
the same way:

* the burden of a player's history scales with how *long* one injury kept him
  out rather than with how many times his body actually failed; and
* every multi-week absence trips the "recurring area" penalty, which exists to
  flag the player who pulls the same hamstring every autumn.

Against the real archive, 1,389 of 3,245 player-season-region groups hold two or
more rows, so the recurrence signal fired on roughly two fifths of them for no
reason beyond an injury lasting more than one week. That is the opposite of what
a manager wants: it penalises the severity of a single healed injury and calls it
a pattern.

This module groups the rows back into *spells* - one uninterrupted absence - so
the model counts injuries rather than paperwork.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fhe.core.health.models import InjuryHistoryEvent
from fhe.core.types import BodyRegion, InjuryDesignation

# Weeks of silence tolerated inside one spell. A player can be listed in week 10,
# go unlisted in week 11 - a bye, or a week he practised fully and was dropped
# from the report - and be listed again in week 12 with the same injury. Two
# weeks keeps that case together while still splitting a September ankle from a
# December one.
SPELL_CONTINUATION_WEEKS: Final = 2

# Last and first week numbers that let a spell bridge a season boundary. An
# injury reported in the final week of one season and again in the opening week
# of the next is one injury, not two, and splitting it would manufacture exactly
# the false recurrence this module exists to remove.
_SEASON_END_WEEK: Final = 17
_SEASON_START_WEEK: Final = 2

# Designations that mean the player did not play. DOUBTFUL is excluded: it is a
# forecast, and the following week's row says what actually happened.
_ABSENT_DESIGNATIONS: Final[frozenset[InjuryDesignation]] = frozenset(
    {
        InjuryDesignation.OUT,
        InjuryDesignation.IR,
        InjuryDesignation.PUP,
        InjuryDesignation.NFI,
        InjuryDesignation.NOT_ACTIVE,
    }
)

# Ordered worst-first so the most serious status observed during a spell is the
# one that describes it.
_SEVERITY_ORDER: Final[tuple[InjuryDesignation, ...]] = (
    InjuryDesignation.IR,
    InjuryDesignation.PUP,
    InjuryDesignation.NFI,
    InjuryDesignation.OUT,
    InjuryDesignation.SUSPENDED,
    InjuryDesignation.DOUBTFUL,
    InjuryDesignation.NOT_ACTIVE,
    InjuryDesignation.DID_NOT_REPORT,
    InjuryDesignation.COVID,
    InjuryDesignation.QUESTIONABLE,
    InjuryDesignation.ACTIVE,
    InjuryDesignation.UNKNOWN,
)
_SEVERITY_RANK: Final[dict[InjuryDesignation, int]] = {
    designation: rank for rank, designation in enumerate(_SEVERITY_ORDER)
}


@dataclass(frozen=True, slots=True)
class InjurySpell:
    """One continuous injury, reconstructed from the reports that named it.

    Args:
        region: Normalised body region shared by every report in the spell.
        first_season: Season the spell was first reported in.
        last_season: Season it was last reported in. Differs from
            ``first_season`` only for an injury that bridged the new year, and
            it is the one recency is measured from, because it says when the
            player was last unavailable.
        first_week: Week of the first report, when weeks are known.
        last_week: Week of the last report, when weeks are known.
        reports: How many weekly rows collapsed into this spell.
        weeks_absent: Reports carrying a designation that means the player did
            not play. A lower bound on games missed, not a count of them.
        games_missed: Games attributed to this spell when the provider supplies
            them, otherwise ``None`` - which means unknown, not zero.
        worst_designation: Most severe status observed during the spell.
        raw_descriptors: Every distinct provider string, never discarded.
    """

    region: BodyRegion
    first_season: int
    last_season: int
    first_week: int | None
    last_week: int | None
    reports: int
    weeks_absent: int
    games_missed: int | None
    worst_designation: InjuryDesignation
    raw_descriptors: tuple[str, ...]

    @property
    def missed_weeks(self) -> int:
        """Best available count of weeks this spell cost, as a lower bound.

        Prefers a provider's explicit ``games_missed`` and otherwise falls back
        to the weekly rows that carried an absent designation. The fallback is
        load-bearing rather than theoretical: nflverse supplies no games-missed
        column at all, so without it the model would have no measure of time
        actually lost - only of how many times a player was hurt, which rates a
        season-ending tear the same as an afternoon on the exercise bike.
        """
        if self.games_missed is not None:
            return self.games_missed
        return self.weeks_absent


def _continues(previous: InjuryHistoryEvent, event: InjuryHistoryEvent) -> bool:
    """Whether ``event`` is a further report of the injury ``previous`` named.

    Both are already known to share a player and a body region.
    """
    if previous.week is None or event.week is None:
        # Without week numbers there is no evidence these are different
        # injuries, and inventing a second one would overstate the history.
        return previous.season == event.season

    if event.season == previous.season:
        return event.week - previous.week <= SPELL_CONTINUATION_WEEKS
    if event.season == previous.season + 1:
        return previous.week >= _SEASON_END_WEEK and event.week <= _SEASON_START_WEEK
    return False


def _build(group: list[InjuryHistoryEvent]) -> InjurySpell:
    """Fold one already-grouped run of reports into a spell."""
    first, last = group[0], group[-1]
    descriptors: list[str] = []
    for event in group:
        if event.raw_descriptor and event.raw_descriptor not in descriptors:
            descriptors.append(event.raw_descriptor)
    return InjurySpell(
        region=first.region,
        first_season=first.season,
        last_season=last.season,
        first_week=first.week,
        last_week=last.week,
        reports=len(group),
        weeks_absent=sum(1 for e in group if e.designation in _ABSENT_DESIGNATIONS),
        games_missed=(
            sum(counted) if (counted := [e.games_missed for e in group if e.games_missed]) else None
        ),
        worst_designation=min(
            (e.designation for e in group),
            key=lambda d: _SEVERITY_RANK.get(d, len(_SEVERITY_ORDER)),
        ),
        raw_descriptors=tuple(descriptors),
    )


def collapse_to_spells(
    events: tuple[InjuryHistoryEvent, ...] | list[InjuryHistoryEvent],
) -> tuple[InjurySpell, ...]:
    """Group weekly injury reports into the distinct injuries behind them.

    Reports join the same spell when they name the same body region and follow
    within :data:`SPELL_CONTINUATION_WEEKS`, including across a season boundary
    for an injury reported at the end of one season and the start of the next.

    Args:
        events: Injury reports for a single player, in any order.

    Returns:
        The reconstructed spells, ordered by when they started.

    Examples:
        >>> from fhe.core.health.models import InjuryHistoryEvent as E
        >>> weekly = tuple(
        ...     E(season=2025, week=w, region=BodyRegion.ARM_ELBOW, raw_descriptor="Elbow")
        ...     for w in (10, 11, 13, 15)
        ... )
        >>> spells = collapse_to_spells(weekly)
        >>> len(spells), spells[0].reports
        (1, 4)
    """
    by_region: dict[BodyRegion, list[InjuryHistoryEvent]] = {}
    for event in events:
        by_region.setdefault(event.region, []).append(event)

    spells: list[InjurySpell] = []
    for region_events in by_region.values():
        # Sort undated reports last: they carry no position in the season, so
        # they must not break a run of dated ones.
        region_events.sort(key=lambda e: (e.season, e.week is None, e.week or 0))
        group = [region_events[0]]
        for event in region_events[1:]:
            if _continues(group[-1], event):
                group.append(event)
            else:
                spells.append(_build(group))
                group = [event]
        spells.append(_build(group))

    spells.sort(key=lambda s: (s.first_season, s.first_week if s.first_week is not None else 0))
    return tuple(spells)
