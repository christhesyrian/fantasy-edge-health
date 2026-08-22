"""Roster construction: what a team still needs to field a legal lineup.

Roster need is what stops the engine recommending a fourth tight end because he
happens to be the highest-VORP player on the board. It is computed by actually
filling the league's declared lineup with the players a team has drafted, then
asking what is left over.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from fhe.core.league import ROSTERABLE_POSITIONS, LeagueSettings
from fhe.core.types import SLOT_ELIGIBILITY, Position, RosterSlot

# Need weight applied when a position has an unfilled *dedicated* starting slot.
# This is the strongest roster signal there is: without it the lineup is illegal.
_NEED_DEDICATED_STARTER: Final = 1.0
# Applied when only flex slots remain that this position is eligible for.
_NEED_FLEX_ONLY: Final = 0.55
# Applied once every starting slot is covered; further picks are depth.
_NEED_BENCH_DEPTH: Final = 0.15
# Once a team holds this many players at a position beyond its starting
# requirement, additional ones are actively redundant.
_SURPLUS_TOLERANCE: Final = 2
_NEED_SURPLUS_PENALTY: Final = 0.05

# Slot fill order: most restrictive first, so a flex is never consumed by a
# player who was the only candidate for a dedicated slot.
_SLOT_FILL_PRIORITY: Final[dict[RosterSlot, int]] = {
    RosterSlot.QB: 0,
    RosterSlot.RB: 0,
    RosterSlot.WR: 0,
    RosterSlot.TE: 0,
    RosterSlot.K: 0,
    RosterSlot.DEF: 0,
    RosterSlot.REC_FLEX: 1,
    RosterSlot.WRRB_FLEX: 1,
    RosterSlot.FLEX: 2,
    RosterSlot.SUPER_FLEX: 3,
}


@dataclass(frozen=True, slots=True)
class RosterNeed:
    """What a team still needs.

    Args:
        unfilled_slots: Starting slots with nobody in them, in fill order.
        filled_slots: How many of each starting slot are covered.
        position_counts: Players held at each position.
        need_by_position: 0-1 urgency per position, consumed by the scorer.
        starters_remaining: Total unfilled starting slots.
    """

    unfilled_slots: tuple[RosterSlot, ...]
    filled_slots: Mapping[RosterSlot, int]
    position_counts: Mapping[Position, int]
    need_by_position: Mapping[Position, float]
    starters_remaining: int = field(default=0)

    def need_for(self, position: Position) -> float:
        """Urgency of adding a player at ``position``."""
        return self.need_by_position.get(position, _NEED_BENCH_DEPTH)

    def is_starter_slot_open_for(self, position: Position) -> bool:
        """Whether an unfilled starting slot could be filled by this position."""
        return any(slot.accepts(position) for slot in self.unfilled_slots)


def _ordered_starting_slots(league: LeagueSettings) -> list[RosterSlot]:
    """Starting slots sorted most-restrictive first."""
    return sorted(
        league.starting_slots,
        key=lambda s: _SLOT_FILL_PRIORITY.get(s, 9),
    )


def compute_roster_need(
    league: LeagueSettings,
    drafted_positions: Sequence[Position],
) -> RosterNeed:
    """Work out what a roster still needs from the positions it already holds.

    Slots are filled greedily, most restrictive first. That ordering is what
    makes the result correct: filling a FLEX before a dedicated RB slot could
    strand a running back in the flex and report a phantom RB need.

    Args:
        league: League lineup configuration.
        drafted_positions: Positions of players already on the roster.

    Returns:
        A :class:`RosterNeed` describing unfilled slots and per-position urgency.
    """
    remaining: dict[Position, int] = {}
    for position in drafted_positions:
        remaining[position] = remaining.get(position, 0) + 1
    position_counts = dict(remaining)

    filled: dict[RosterSlot, int] = {}
    unfilled: list[RosterSlot] = []

    for slot in _ordered_starting_slots(league):
        eligible = [p for p in SLOT_ELIGIBILITY.get(slot, frozenset()) if remaining.get(p, 0) > 0]
        if not eligible:
            unfilled.append(slot)
            continue
        # Consume from the position with the greatest surplus, so scarce
        # single-eligibility players stay available for their own slots.
        chosen = max(eligible, key=lambda p: remaining[p])
        remaining[chosen] -= 1
        filled[slot] = filled.get(slot, 0) + 1

    need: dict[Position, float] = {}
    for position in ROSTERABLE_POSITIONS:
        dedicated_open = any(
            slot.accepts(position) and len(SLOT_ELIGIBILITY.get(slot, frozenset())) == 1
            for slot in unfilled
        )
        flex_open = any(slot.accepts(position) for slot in unfilled)

        if dedicated_open:
            value = _NEED_DEDICATED_STARTER
        elif flex_open:
            value = _NEED_FLEX_ONLY
        else:
            value = _NEED_BENCH_DEPTH

        # Damp the need once a team is already stacked at the position.
        starters_required = league.dedicated_starters.get(position, 0)
        surplus = position_counts.get(position, 0) - starters_required
        if surplus >= _SURPLUS_TOLERANCE:
            value = min(value, _NEED_SURPLUS_PENALTY)

        need[position] = round(value, 3)

    return RosterNeed(
        unfilled_slots=tuple(unfilled),
        filled_slots=filled,
        position_counts=position_counts,
        need_by_position=need,
        starters_remaining=len(unfilled),
    )
