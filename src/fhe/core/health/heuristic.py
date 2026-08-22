"""Transparent heuristic availability-risk scorer.

Design contract
---------------
* **Every constant is named and justified.** No magic numbers appear inline.
* **Every point of risk is attributable.** The returned components sum to the
  raw score, so the UI can always show *why* a player scored what they scored.
* **Missing data lowers confidence, it does not invent risk.** A player with no
  injury history and no workload record is not "safe"; they are *unmeasured*,
  and the assessment says so.
* **No clinical claims.** The scorer consumes designations, practice reports and
  historical availability. It never asserts a diagnosis, a severity, or that a
  future injury will happen.

The scorer is deliberately additive rather than multiplicative: an additive model
with a clamped total is far easier to explain under draft-clock pressure, and the
explainability requirement outranks marginal accuracy here. The ML model in
:mod:`fhe.ml` is where a non-linear fit is allowed to earn its place.
"""

from __future__ import annotations

from typing import Final

from fhe.core.health.models import (
    HealthAssessment,
    HealthInputs,
    RiskComponent,
)
from fhe.core.injury.practice import practice_trajectory
from fhe.core.types import (
    BodyRegion,
    InjuryDesignation,
    Position,
    PracticeStatus,
    PracticeTrajectory,
)

MODEL_VERSION: Final = "heuristic-v1"

SCORE_MIN: Final = 0.0
SCORE_MAX: Final = 100.0

# --------------------------------------------------------------------------
# Current designation
# --------------------------------------------------------------------------
# The single strongest signal available: an official game-status designation is
# a direct statement about near-term availability. Values are spaced so that a
# season-ending designation cannot be offset by any combination of soft signals.
_DESIGNATION_POINTS: Final[dict[InjuryDesignation, float]] = {
    InjuryDesignation.IR: 78.0,
    InjuryDesignation.PUP: 62.0,
    InjuryDesignation.NFI: 55.0,
    InjuryDesignation.OUT: 48.0,
    InjuryDesignation.SUSPENDED: 40.0,
    InjuryDesignation.DOUBTFUL: 32.0,
    InjuryDesignation.NOT_ACTIVE: 25.0,
    InjuryDesignation.DID_NOT_REPORT: 20.0,
    InjuryDesignation.COVID: 15.0,
    InjuryDesignation.QUESTIONABLE: 14.0,
    InjuryDesignation.UNKNOWN: 0.0,
    InjuryDesignation.ACTIVE: 0.0,
}

# --------------------------------------------------------------------------
# Practice trajectory
# --------------------------------------------------------------------------
# A player already carrying a designation who is trending the wrong way in
# practice is materially more likely to sit. Improvement earns a discount, but a
# smaller one than the penalty: recovering practice participation is weaker
# evidence of availability than declining participation is of absence.
_TRAJECTORY_POINTS: Final[dict[PracticeTrajectory, float]] = {
    PracticeTrajectory.WORSENING: 12.0,
    PracticeTrajectory.STABLE: 0.0,
    PracticeTrajectory.IMPROVING: -8.0,
    PracticeTrajectory.INSUFFICIENT_DATA: 0.0,
}
# A run of DNPs is a standalone signal regardless of direction.
_CONSECUTIVE_DNP_POINTS: Final = 6.0
_MAX_DNP_RUN_COUNTED: Final = 3

# --------------------------------------------------------------------------
# Injury history
# --------------------------------------------------------------------------
# Seasons of history the scorer considers. Older events say little about a
# player's current body.
_HISTORY_LOOKBACK_SEASONS: Final = 3
# Per-season recency weights, most recent first.
_RECENCY_WEIGHTS: Final[tuple[float, ...]] = (1.0, 0.6, 0.3)
# Points per weighted distinct injury event, capped below.
_POINTS_PER_WEIGHTED_EVENT: Final = 2.2
_MAX_HISTORY_POINTS: Final = 18.0

# Repeated injuries to the same region are the most durable predictor in the
# public literature, and soft-tissue recurrence is stronger still.
_RECURRENCE_POINTS: Final = 5.0
_SOFT_TISSUE_RECURRENCE_POINTS: Final = 8.0
_MAX_RECURRENCE_POINTS: Final = 16.0

# Games actually missed is the outcome the model ultimately cares about.
_POINTS_PER_GAME_MISSED: Final = 1.1
_MAX_GAMES_MISSED_POINTS: Final = 14.0

# --------------------------------------------------------------------------
# Age
# --------------------------------------------------------------------------
# Age at which availability risk begins rising for each position, and the risk
# added per year beyond it. Running backs decline earliest and steepest; this is
# one of the most consistently observed effects in football analytics.
_AGE_THRESHOLD: Final[dict[Position, float]] = {
    Position.RB: 26.0,
    Position.WR: 29.0,
    Position.TE: 30.0,
    Position.QB: 35.0,
    Position.K: 38.0,
    Position.DEF: 99.0,
    Position.UNKNOWN: 30.0,
}
_AGE_POINTS_PER_YEAR: Final[dict[Position, float]] = {
    Position.RB: 3.4,
    Position.WR: 2.2,
    Position.TE: 2.0,
    Position.QB: 1.6,
    Position.K: 0.8,
    Position.DEF: 0.0,
    Position.UNKNOWN: 2.0,
}
_MAX_AGE_POINTS: Final = 15.0

# Rookies carry mild additional uncertainty: no professional durability record.
_ROOKIE_UNCERTAINTY_POINTS: Final = 2.5

# --------------------------------------------------------------------------
# Workload
# --------------------------------------------------------------------------
# High touch volume raises exposure. The threshold is position-specific because
# 18 touches means something very different for a RB than for a WR.
_HIGH_TOUCH_THRESHOLD: Final[dict[Position, float]] = {
    Position.RB: 18.0,
    Position.WR: 9.0,
    Position.TE: 7.0,
}
_HIGH_WORKLOAD_POINTS: Final = 4.0
# Conversely, a full season of heavy usage is *evidence of durability*.
_DURABILITY_GAMES_THRESHOLD: Final = 16
_DURABILITY_DISCOUNT: Final = -5.0

# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------
# Weight each input source contributes to the confidence measure. These sum to
# 1.0 and describe *data completeness*, not correctness.
_CONFIDENCE_WEIGHTS: Final[dict[str, float]] = {
    "designation": 0.25,
    "injury_history": 0.25,
    "workload": 0.20,
    "age": 0.15,
    "practice": 0.15,
}

# --------------------------------------------------------------------------
# Availability mapping
# --------------------------------------------------------------------------
# Maps a 0-100 risk score onto an estimated share of games available. Anchored
# so that a clean profile sits near a realistic league-average availability
# rather than at a fictitious 100%: even healthy NFL players miss games.
_BASELINE_AVAILABILITY: Final = 0.94
_AVAILABILITY_FLOOR: Final = 0.05
# Share of availability that risk can erode.
_AVAILABILITY_SENSITIVITY: Final = 0.89

_LIMITATIONS: Final[tuple[str, ...]] = (
    "Estimates fantasy availability risk, not medical outcomes.",
    "Derived from public injury reports, which omit severity and prognosis.",
    "Absence of an injury report is not evidence that a player is healthy.",
)


def _clamp(value: float, low: float, high: float) -> float:
    """Constrain ``value`` to ``[low, high]``."""
    return max(low, min(high, value))


def _designation_component(inputs: HealthInputs) -> RiskComponent | None:
    """Risk from the current official designation."""
    points = _DESIGNATION_POINTS.get(inputs.designation, 0.0)
    if points <= 0:
        return None
    region = inputs.current_injury_region
    region_text = (
        f" ({region.value.replace('_', ' ').lower()})"
        if region and region is not BodyRegion.OTHER_UNKNOWN
        else ""
    )
    return RiskComponent(
        name="current_designation",
        label="Current designation",
        points=points,
        detail=f"Listed {inputs.designation.value}{region_text}.",
    )


def _practice_components(
    inputs: HealthInputs,
) -> tuple[list[RiskComponent], PracticeTrajectory]:
    """Risk from practice participation and its direction."""
    trajectory = practice_trajectory(inputs.practice_statuses)
    components: list[RiskComponent] = []

    points = _TRAJECTORY_POINTS[trajectory]
    if points:
        direction = "worsening" if points > 0 else "improving"
        components.append(
            RiskComponent(
                name="practice_trajectory",
                label="Practice trend",
                points=points,
                detail=f"Practice participation {direction} across recent reports.",
            )
        )

    # Count the trailing run of DNPs.
    run = 0
    for status in reversed(inputs.practice_statuses):
        if status is PracticeStatus.DNP:
            run += 1
        elif status is PracticeStatus.UNKNOWN:
            continue
        else:
            break
    if run:
        counted = min(run, _MAX_DNP_RUN_COUNTED)
        components.append(
            RiskComponent(
                name="consecutive_dnp",
                label="Missed practices",
                points=_CONSECUTIVE_DNP_POINTS * counted,
                detail=f"{run} consecutive practice{'s' if run != 1 else ''} missed.",
            )
        )
    return components, trajectory


def _history_components(inputs: HealthInputs) -> list[RiskComponent]:
    """Risk from prior injury events, recurrence, and games actually missed."""
    components: list[RiskComponent] = []
    if not inputs.injury_history:
        return components

    reference_season = inputs.current_season or max(e.season for e in inputs.injury_history)

    # Only injuries count toward burden; rest days and personal matters do not.
    injuries = [
        e
        for e in inputs.injury_history
        if e.region not in {BodyRegion.REST, BodyRegion.NON_INJURY}
        and 0 <= reference_season - e.season < _HISTORY_LOOKBACK_SEASONS
    ]
    if not injuries:
        return components

    weighted = 0.0
    for event in injuries:
        age_in_seasons = reference_season - event.season
        weighted += _RECENCY_WEIGHTS[min(age_in_seasons, len(_RECENCY_WEIGHTS) - 1)]

    burden = min(_MAX_HISTORY_POINTS, weighted * _POINTS_PER_WEIGHTED_EVENT)
    if burden > 0:
        components.append(
            RiskComponent(
                name="injury_burden",
                label="Injury history",
                points=burden,
                detail=(
                    f"{len(injuries)} injury report"
                    f"{'s' if len(injuries) != 1 else ''} in the last "
                    f"{_HISTORY_LOOKBACK_SEASONS} seasons, recency-weighted."
                ),
            )
        )

    # Recurrence by region.
    by_region: dict[BodyRegion, int] = {}
    for event in injuries:
        if event.region in {BodyRegion.UNDISCLOSED, BodyRegion.OTHER_UNKNOWN}:
            continue
        by_region[event.region] = by_region.get(event.region, 0) + 1

    recurrence = 0.0
    recurring: list[str] = []
    for region, count in by_region.items():
        if count < 2:
            continue
        per_event = _SOFT_TISSUE_RECURRENCE_POINTS if region.is_soft_tissue else _RECURRENCE_POINTS
        recurrence += per_event * (count - 1)
        recurring.append(f"{region.value.replace('_', ' ').lower()} x{count}")
    if recurrence > 0:
        components.append(
            RiskComponent(
                name="recurrent_injury",
                label="Recurring area",
                points=min(_MAX_RECURRENCE_POINTS, recurrence),
                detail=f"Repeated reports in the same area: {', '.join(sorted(recurring))}.",
            )
        )

    # Games actually missed.
    missed = sum(e.games_missed for e in injuries if e.games_missed)
    if missed:
        components.append(
            RiskComponent(
                name="games_missed",
                label="Games missed",
                points=min(_MAX_GAMES_MISSED_POINTS, missed * _POINTS_PER_GAME_MISSED),
                detail=f"{missed} game{'s' if missed != 1 else ''} missed to injury recently.",
            )
        )
    return components


def _age_components(inputs: HealthInputs) -> list[RiskComponent]:
    """Risk from the position-specific ageing curve and rookie uncertainty."""
    components: list[RiskComponent] = []
    if inputs.age is not None:
        threshold = _AGE_THRESHOLD.get(inputs.position, _AGE_THRESHOLD[Position.UNKNOWN])
        per_year = _AGE_POINTS_PER_YEAR.get(inputs.position, _AGE_POINTS_PER_YEAR[Position.UNKNOWN])
        excess = inputs.age - threshold
        if excess > 0:
            points = min(_MAX_AGE_POINTS, excess * per_year)
            components.append(
                RiskComponent(
                    name="age_curve",
                    label="Age",
                    points=points,
                    detail=(
                        f"Age {inputs.age:.0f} is {excess:.0f} year"
                        f"{'s' if excess >= 2 else ''} past the {inputs.position.value} "
                        f"risk threshold of {threshold:.0f}."
                    ),
                )
            )

    if inputs.years_experience == 0:
        components.append(
            RiskComponent(
                name="rookie_uncertainty",
                label="Rookie",
                points=_ROOKIE_UNCERTAINTY_POINTS,
                detail="No professional durability record yet.",
            )
        )
    return components


def _workload_components(inputs: HealthInputs) -> list[RiskComponent]:
    """Exposure risk from heavy usage, offset by demonstrated durability."""
    components: list[RiskComponent] = []
    workload = inputs.workload
    if workload is None:
        return components

    touches = workload.touches_per_game
    threshold = _HIGH_TOUCH_THRESHOLD.get(inputs.position)
    if touches is not None and threshold is not None and touches > threshold:
        components.append(
            RiskComponent(
                name="workload_exposure",
                label="Heavy usage",
                points=_HIGH_WORKLOAD_POINTS,
                detail=(
                    f"{touches:.1f} touches per game exceeds the "
                    f"{inputs.position.value} exposure threshold of {threshold:.0f}."
                ),
            )
        )

    if workload.games_played is not None and workload.games_played >= _DURABILITY_GAMES_THRESHOLD:
        components.append(
            RiskComponent(
                name="demonstrated_durability",
                label="Durability",
                points=_DURABILITY_DISCOUNT,
                detail=f"Played {workload.games_played} games in the most recent season.",
            )
        )
    return components


def _confidence(inputs: HealthInputs) -> float:
    """How much of the expected input signal was actually present."""
    present = 0.0
    if inputs.designation is not InjuryDesignation.UNKNOWN:
        present += _CONFIDENCE_WEIGHTS["designation"]
    if inputs.injury_history:
        present += _CONFIDENCE_WEIGHTS["injury_history"]
    if inputs.workload is not None and inputs.workload.games_played is not None:
        present += _CONFIDENCE_WEIGHTS["workload"]
    if inputs.age is not None:
        present += _CONFIDENCE_WEIGHTS["age"]
    if any(s is not PracticeStatus.UNKNOWN for s in inputs.practice_statuses):
        present += _CONFIDENCE_WEIGHTS["practice"]
    return round(_clamp(present, 0.0, 1.0), 3)


def _availability_from_risk(risk_score: float) -> float:
    """Map a risk score onto an estimated share of games available."""
    fraction = risk_score / SCORE_MAX
    estimate = _BASELINE_AVAILABILITY - _AVAILABILITY_SENSITIVITY * fraction
    return round(_clamp(estimate, _AVAILABILITY_FLOOR, 1.0), 3)


def score_health(inputs: HealthInputs) -> HealthAssessment:
    """Produce a decomposable availability-risk assessment.

    The returned ``components`` sum to the pre-clamp score, so the UI can always
    render the arithmetic that produced the headline number.

    Args:
        inputs: Everything known about the player at assessment time.

    Returns:
        A :class:`HealthAssessment` with score, availability estimate,
        confidence, and the signed component breakdown.
    """
    components: list[RiskComponent] = []

    designation = _designation_component(inputs)
    if designation is not None:
        components.append(designation)

    practice, trajectory = _practice_components(inputs)
    components.extend(practice)
    components.extend(_history_components(inputs))
    components.extend(_age_components(inputs))
    components.extend(_workload_components(inputs))

    raw = round(sum(c.points for c in components), 1)
    risk_score = round(_clamp(raw, SCORE_MIN, SCORE_MAX), 1)

    limitations = list(_LIMITATIONS)
    confidence = _confidence(inputs)
    if confidence < 0.5:
        limitations.append(
            "Limited data available for this player; treat the score as provisional."
        )

    return HealthAssessment(
        player_uuid=inputs.player_uuid,
        risk_score=risk_score,
        raw_score=raw,
        availability_estimate=_availability_from_risk(risk_score),
        confidence=confidence,
        components=tuple(sorted(components, key=lambda c: abs(c.points), reverse=True)),
        practice_trajectory=trajectory,
        model_version=MODEL_VERSION,
        limitations=tuple(limitations),
    )
