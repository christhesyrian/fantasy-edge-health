"""Value over replacement.

A raw projection is not a draft signal. 280 projected points is elite for a tight
end and replacement-level for a quarterback, and the difference is entirely a
property of the *league*, not the player. VORP converts a projection into the
only thing that matters at the table: how many points this player adds over the
player you could have had for free at the same position.

Baseline choice
---------------
The replacement baseline is computed **once, from the full pre-draft pool**,
rather than being recomputed against the shrinking pool of available players.

A dynamic baseline sounds more responsive, but it double-counts: the draft engine
already models draft dynamics explicitly through positional scarcity and
next-pick survival. Recomputing replacement level as players come off the board
would fold the same effect into VORP as well, over-weighting runs. A static
baseline keeps VORP a stable, interpretable measure of talent, and lets the
dynamic terms do the dynamic work.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from fhe.core.draft.models import DraftablePlayer
from fhe.core.league import ROSTERABLE_POSITIONS, LeagueSettings
from fhe.core.types import Position


@dataclass(frozen=True, slots=True)
class ReplacementBaseline:
    """Replacement-level projected points for each position.

    Args:
        points_by_position: Replacement-level projection per position.
        replacement_rank: The rank used to pick each baseline.
        players_considered: How many projected players fed the calculation.
        max_vorp: The largest value over replacement in the *full pre-draft*
            pool. This is the scale the engine normalises against, and it is
            deliberately fixed rather than recomputed from whoever is still
            available. Normalising against the current board would make the best
            remaining player score 100% of the value weight at every pick, so a
            round-14 defense would look exactly as valuable as the 1.01 pick.
    """

    points_by_position: dict[Position, float]
    replacement_rank: dict[Position, int]
    players_considered: int
    max_vorp: float = 0.0

    def points_for(self, position: Position) -> float:
        """Replacement-level points at a position; 0.0 for unvalued positions."""
        return self.points_by_position.get(position, 0.0)


def _projected(players: Iterable[DraftablePlayer], position: Position) -> list[float]:
    """Descending projected points for players at a position that have one."""
    points = [
        p.projected_points
        for p in players
        if p.position is position and p.projected_points is not None
    ]
    return sorted(points, reverse=True)


def compute_replacement_baseline(
    players: Sequence[DraftablePlayer],
    league: LeagueSettings,
) -> ReplacementBaseline:
    """Determine replacement-level production for each position.

    The baseline is the projection of the player sitting exactly at the league's
    replacement rank. If a position has fewer projected players than that rank,
    the worst available projection is used instead - a shallow pool makes
    replacement level *lower*, never undefined.

    Args:
        players: The full player pool, ideally pre-draft.
        league: League configuration, which determines replacement rank.

    Returns:
        The baseline, carrying the ranks it used so the UI can explain itself.
    """
    points: dict[Position, float] = {}
    ranks = league.replacement_rank

    for position in ROSTERABLE_POSITIONS:
        projections = _projected(players, position)
        if not projections:
            points[position] = 0.0
            continue
        rank = ranks.get(position, 1)
        index = min(rank, len(projections)) - 1
        points[position] = projections[index]

    baseline = ReplacementBaseline(
        points_by_position=points,
        replacement_rank=dict(ranks),
        players_considered=sum(1 for p in players if p.has_projection),
    )
    vorps = [v for v in (value_over_replacement(p, baseline) for p in players) if v is not None]
    return ReplacementBaseline(
        points_by_position=points,
        replacement_rank=dict(ranks),
        players_considered=baseline.players_considered,
        max_vorp=max(vorps) if vorps else 0.0,
    )


def value_over_replacement(
    player: DraftablePlayer,
    baseline: ReplacementBaseline,
) -> float | None:
    """Projected points above replacement level, or ``None`` without a projection.

    Returning ``None`` rather than 0.0 is deliberate: "we do not know this
    player's value" and "this player is exactly replacement level" are different
    statements, and the UI shows them differently.
    """
    if player.projected_points is None:
        return None
    return round(player.projected_points - baseline.points_for(player.position), 2)
