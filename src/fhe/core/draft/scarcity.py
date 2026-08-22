"""Positional scarcity and tier detection.

Two related questions the war room has to answer instantly:

* **Tiers** - is there a cliff right below this player, or five equivalent
  options? Taking the last member of a tier is far more valuable than taking the
  first member of the next one.
* **Scarcity** - how fast is startable talent at this position disappearing
  relative to how long I have to wait?
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import Final

from fhe.core.draft.models import DraftablePlayer
from fhe.core.league import VALUED_POSITIONS
from fhe.core.types import Position

# A tier boundary is declared when the drop to the next player exceeds the
# typical drop by this multiple. Derived from the pool's own gap distribution, so
# it adapts to scoring format and projection scale instead of hard-coding points.
_TIER_GAP_MULTIPLIER: Final = 2.0
# Guards against declaring tiers inside noise when projections are nearly flat.
_MIN_TIER_GAP_POINTS: Final = 4.0
# Players beyond this positional depth are not tiered; they are all bench darts.
_MAX_TIERED_DEPTH: Final = 60


@dataclass(frozen=True, slots=True)
class PositionTier:
    """A contiguous group of players of near-equivalent projected value."""

    position: Position
    tier: int
    player_uuids: tuple[str, ...]
    top_points: float
    bottom_points: float

    @property
    def size(self) -> int:
        """How many players remain in this tier."""
        return len(self.player_uuids)


@dataclass(frozen=True, slots=True)
class PositionScarcity:
    """Scarcity summary for one position at a point in the draft.

    Args:
        position: The position described.
        available_starters: Available players still projected above replacement.
        tier_size_remaining: Players left in the *current best* tier.
        next_tier_dropoff: Projected points lost by falling out of the current
            tier into the next one. ``None`` when there is no next tier.
        expected_gone_before_next_pick: How many of this position are expected to
            be drafted before the user's next selection.
        scarcity_index: 0-1, where 1 means the current tier is about to vanish
            before the user picks again.
    """

    position: Position
    available_starters: int
    tier_size_remaining: int
    next_tier_dropoff: float | None
    expected_gone_before_next_pick: float
    scarcity_index: float


def build_tiers(
    players: Sequence[DraftablePlayer],
    position: Position,
) -> tuple[PositionTier, ...]:
    """Group available players at a position into value tiers.

    A boundary is placed wherever the projection drop to the next player is both
    larger than :data:`_MIN_TIER_GAP_POINTS` and more than
    :data:`_TIER_GAP_MULTIPLIER` times the median drop across the position.
    """
    ranked = sorted(
        (p for p in players if p.position is position and p.has_projection),
        key=lambda p: p.projected_points or 0.0,
        reverse=True,
    )[:_MAX_TIERED_DEPTH]
    if not ranked:
        return ()

    points = [p.projected_points or 0.0 for p in ranked]
    gaps = [points[i] - points[i + 1] for i in range(len(points) - 1)]
    typical_gap = median(gaps) if gaps else 0.0
    threshold = max(_MIN_TIER_GAP_POINTS, typical_gap * _TIER_GAP_MULTIPLIER)

    tiers: list[PositionTier] = []
    current: list[DraftablePlayer] = [ranked[0]]
    tier_number = 1

    for index in range(1, len(ranked)):
        gap = points[index - 1] - points[index]
        if gap >= threshold:
            tiers.append(_make_tier(position, tier_number, current))
            tier_number += 1
            current = []
        current.append(ranked[index])

    if current:
        tiers.append(_make_tier(position, tier_number, current))
    return tuple(tiers)


def _make_tier(
    position: Position, tier_number: int, members: list[DraftablePlayer]
) -> PositionTier:
    """Build a tier record from its members."""
    projections = [m.projected_points or 0.0 for m in members]
    return PositionTier(
        position=position,
        tier=tier_number,
        player_uuids=tuple(m.player_uuid for m in members),
        top_points=max(projections),
        bottom_points=min(projections),
    )


def compute_scarcity(
    available: Sequence[DraftablePlayer],
    *,
    picks_until_next_turn: int | None,
    replacement_points: dict[Position, float],
) -> dict[Position, PositionScarcity]:
    """Summarise how quickly each position's usable talent is disappearing.

    ``expected_gone_before_next_pick`` is estimated from the position's share of
    the remaining pool: if running backs are 30% of the players likely to be
    taken and 14 picks happen before your turn, roughly 4 running backs go.
    That is a coarse prior, refined by the per-player survival model in
    :mod:`fhe.core.draft.survival`, which uses ADP directly.
    """
    result: dict[Position, PositionScarcity] = {}
    wait = picks_until_next_turn or 0

    # Share of upcoming picks each position is expected to absorb, estimated from
    # the composition of the startable players still on the board.
    startable_by_position: dict[Position, int] = {}
    for position in VALUED_POSITIONS:
        baseline = replacement_points.get(position, 0.0)
        startable_by_position[position] = sum(
            1
            for p in available
            if p.position is position
            and p.has_projection
            and (p.projected_points or 0.0) > baseline
        )
    total_startable = sum(startable_by_position.values())

    for position in VALUED_POSITIONS:
        tiers = build_tiers(available, position)
        top_tier = tiers[0] if tiers else None
        next_tier = tiers[1] if len(tiers) > 1 else None

        dropoff = (
            round(top_tier.bottom_points - next_tier.top_points, 2)
            if top_tier and next_tier
            else None
        )

        share = startable_by_position[position] / total_startable if total_startable else 0.0
        expected_gone = round(share * wait, 2)

        tier_size = top_tier.size if top_tier else 0
        if tier_size == 0:
            scarcity_index = 1.0 if startable_by_position[position] == 0 else 0.0
        else:
            scarcity_index = round(min(1.0, expected_gone / tier_size), 3)

        result[position] = PositionScarcity(
            position=position,
            available_starters=startable_by_position[position],
            tier_size_remaining=tier_size,
            next_tier_dropoff=dropoff,
            expected_gone_before_next_pick=expected_gone,
            scarcity_index=scarcity_index,
        )
    return result
