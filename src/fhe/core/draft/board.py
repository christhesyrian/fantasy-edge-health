"""Assembly of the war-room view from a ranked board.

Separated from :mod:`fhe.core.draft.engine` because these are *presentation*
decisions - which four players to headline, which alerts to raise - while the
engine owns the scoring. Keeping them apart means the scoring model can be
retuned without touching what the screen shows, and vice versa.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Final

from fhe.core.draft.engine import (
    _SAFE_MAX_RISK,
    _SAFE_MAX_VOLATILITY,
    _SAFE_MIN_CONFIDENCE,
    _UPSIDE_MAX_AGE,
    _UPSIDE_MAX_EXPERIENCE,
    _UPSIDE_MIN_VOLATILITY,
    PlayerRecommendation,
)
from fhe.core.draft.models import DraftablePlayer
from fhe.core.draft.scarcity import PositionScarcity
from fhe.core.types import Position

# Alert thresholds.
_TIER_CLIFF_MAX_REMAINING: Final = 2
_TIER_CLIFF_MIN_DROPOFF: Final = 15.0
_ADP_FALLER_MIN_DELTA: Final = 12.0
_PICK_PROXIMITY_WARNING: Final = 3
_MAX_ALERTS: Final = 6


@unique
class AlertLevel(StrEnum):
    """Severity of a war-room alert."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class DraftAlert:
    """A single actionable notice for the user."""

    key: str
    level: AlertLevel
    message: str
    position: Position | None = None
    player_uuid: str | None = None


@dataclass(frozen=True, slots=True)
class DraftBoard:
    """The complete war-room state for one pick.

    Args:
        recommendations: Every available player, best first.
        best_pick: Highest overall score.
        safest_pick: Best player whose availability risk is low and measured.
        highest_upside: Best young/ascending player by raw value.
        best_value: Largest positive gap between market ADP and model rank.
        scarcity: Per-position scarcity summary.
        alerts: Ordered notices, most severe first.
        picks_until_user_turn: Selections before the user is on the clock.
    """

    recommendations: tuple[PlayerRecommendation, ...]
    best_pick: PlayerRecommendation | None
    safest_pick: PlayerRecommendation | None
    highest_upside: PlayerRecommendation | None
    best_value: PlayerRecommendation | None
    scarcity: dict[Position, PositionScarcity]
    alerts: tuple[DraftAlert, ...] = field(default=())
    picks_until_user_turn: int | None = None
    current_pick: int | None = None
    next_user_pick: int | None = None


def _pick_safest(
    recommendations: Sequence[PlayerRecommendation],
    players_by_uuid: dict[str, DraftablePlayer],
) -> PlayerRecommendation | None:
    """Best-scoring player who is available *and* steady, both measured.

    Two things make a pick safe, and availability was only one of them. A
    player who misses nothing but swings between three points and thirty is not
    a safe start, so week-to-week volatility is now part of the test.

    Both must be *measured*. An unmeasured player is not safe, they are
    unknown, and presenting them here would be exactly the false confidence
    this product exists to avoid — which is why an absent volatility excludes a
    player from this slot rather than counting as calm.
    """
    available: list[tuple[PlayerRecommendation, float | None]] = []
    for rec in recommendations:
        if rec.health_risk is None or rec.health_risk > _SAFE_MAX_RISK:
            continue
        player = players_by_uuid.get(rec.player_uuid)
        health = player.health if player else None
        if health is None or health.confidence < _SAFE_MIN_CONFIDENCE:
            continue
        volatility = player.usage.volatility if player and player.usage else None
        available.append((rec, volatility))
    if not available:
        return None

    # Prefer the measured and steady. But when *nobody* has a measured
    # volatility — no weekly stats ingested, or a synthetic pool — the signal
    # simply is not available, and withholding the slot entirely would be
    # withholding a useful answer over a missing input rather than a doubtful
    # one. Degrade to availability alone, which is what this slot meant before.
    steady = [
        (rec, volatility)
        for rec, volatility in available
        if volatility is not None and volatility <= _SAFE_MAX_VOLATILITY
    ]
    if steady:
        return max(steady, key=lambda pair: (pair[0].overall_score, -(pair[1] or 0.0)))[0]
    if any(volatility is not None for _, volatility in available):
        # Volatility *is* measured here, and nobody cleared the bar. Saying so
        # by leaving the slot empty is more honest than promoting the least bad.
        return None
    return max(available, key=lambda pair: (pair[0].overall_score, -(pair[0].health_risk or 0.0)))[
        0
    ]


def _pick_upside(
    recommendations: Sequence[PlayerRecommendation],
    players_by_uuid: dict[str, DraftablePlayer],
) -> PlayerRecommendation | None:
    """Best young, early-career, or high-variance player by raw value.

    "Upside" means room to outperform a projection. That comes from being
    early in a career, and it also comes from a scoring pattern with big weeks
    in it — the same volatility that disqualifies a player from ``safest_pick``
    is what qualifies them here. Risk is deliberately *not* discounted, because
    that is what distinguishes this slot from ``best_pick``.
    """
    candidates = []
    for rec in recommendations:
        player = players_by_uuid.get(rec.player_uuid)
        if player is None or rec.vorp is None:
            continue
        young = player.age is not None and player.age <= _UPSIDE_MAX_AGE
        early = (
            player.years_experience is not None
            and player.years_experience <= _UPSIDE_MAX_EXPERIENCE
        )
        volatility = player.usage.volatility if player.usage else None
        swings = volatility is not None and volatility >= _UPSIDE_MIN_VOLATILITY
        if young or early or swings:
            candidates.append(rec)
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.vorp or 0.0, r.overall_score))


def _pick_best_value(
    recommendations: Sequence[PlayerRecommendation],
) -> PlayerRecommendation | None:
    """Largest positive gap between where the market drafts him and our rank."""
    candidates = [r for r in recommendations if r.adp_value is not None and r.adp_value > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.adp_value or 0.0, r.overall_score))


def _build_alerts(
    recommendations: Sequence[PlayerRecommendation],
    scarcity: dict[Position, PositionScarcity],
    *,
    picks_until_user_turn: int | None,
) -> tuple[DraftAlert, ...]:
    """Derive alerts from board state. Every alert is a structured fact."""
    alerts: list[DraftAlert] = []

    if picks_until_user_turn is not None and 0 < picks_until_user_turn <= _PICK_PROXIMITY_WARNING:
        alerts.append(
            DraftAlert(
                key="pick_approaching",
                level=AlertLevel.CRITICAL,
                message=(
                    f"Your pick is in {picks_until_user_turn} selection"
                    f"{'s' if picks_until_user_turn != 1 else ''}."
                ),
            )
        )

    for position, summary in scarcity.items():
        if (
            0 < summary.tier_size_remaining <= _TIER_CLIFF_MAX_REMAINING
            and summary.next_tier_dropoff is not None
            and summary.next_tier_dropoff >= _TIER_CLIFF_MIN_DROPOFF
        ):
            alerts.append(
                DraftAlert(
                    key=f"tier_cliff_{position.value.lower()}",
                    level=AlertLevel.WARNING,
                    message=(
                        f"Only {summary.tier_size_remaining} "
                        f"{position.value} left in this tier - "
                        f"{summary.next_tier_dropoff:.0f} projected points to the next one."
                    ),
                    position=position,
                )
            )
        elif summary.available_starters == 0:
            alerts.append(
                DraftAlert(
                    key=f"position_exhausted_{position.value.lower()}",
                    level=AlertLevel.INFO,
                    message=f"No starter-calibre {position.value} left on the board.",
                    position=position,
                )
            )

    for rec in recommendations[:15]:
        if rec.adp_value is not None and rec.adp_value >= _ADP_FALLER_MIN_DELTA:
            alerts.append(
                DraftAlert(
                    key=f"faller_{rec.player_uuid}",
                    level=AlertLevel.INFO,
                    message=(
                        f"{rec.name} has fallen {rec.adp_value:.0f} picks past "
                        "where the market drafts him."
                    ),
                    position=rec.position,
                    player_uuid=rec.player_uuid,
                )
            )

    severity = {AlertLevel.CRITICAL: 0, AlertLevel.WARNING: 1, AlertLevel.INFO: 2}
    alerts.sort(key=lambda a: severity[a.level])
    return tuple(alerts[:_MAX_ALERTS])


def build_board(
    recommendations: Sequence[PlayerRecommendation],
    available: Sequence[DraftablePlayer],
    scarcity: dict[Position, PositionScarcity],
    *,
    picks_until_user_turn: int | None = None,
    current_pick: int | None = None,
    next_user_pick: int | None = None,
) -> DraftBoard:
    """Assemble the complete war-room view."""
    players_by_uuid = {p.player_uuid: p for p in available}
    return DraftBoard(
        recommendations=tuple(recommendations),
        best_pick=recommendations[0] if recommendations else None,
        safest_pick=_pick_safest(recommendations, players_by_uuid),
        highest_upside=_pick_upside(recommendations, players_by_uuid),
        best_value=_pick_best_value(recommendations),
        scarcity=scarcity,
        alerts=_build_alerts(
            recommendations, scarcity, picks_until_user_turn=picks_until_user_turn
        ),
        picks_until_user_turn=picks_until_user_turn,
        current_pick=current_pick,
        next_user_pick=next_user_pick,
    )
