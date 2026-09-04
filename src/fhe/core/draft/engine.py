"""The draft recommendation engine.

Scoring model
-------------
The overall score is an **additive** combination of weighted, normalised
components. Additivity is a deliberate product decision: under a draft clock the
user has seconds to decide whether to trust a number, and an additive score can
always be shown as the arithmetic that produced it. A multiplicative or learned
blend might fit marginally better and would be impossible to defend in ten
seconds.

    overall = w_vorp     * vorp_normalised          (signed: [-1, 1])
            + w_scarcity * positional_scarcity
            + w_need     * roster_need
            + w_adp      * adp_value_normalised
            + w_urgency  * take_now_probability * vorp_normalised
            - w_health   * availability_risk
            - w_bye      * bye_collision

Resolving the ADP/rank circularity
----------------------------------
``adp_value`` is defined as ``market_adp - model_rank``, but ``model_rank`` is an
output of the very score that ``adp_value`` feeds. The engine resolves this with
an explicit two-pass evaluation:

1. Score every player *without* the ADP term to obtain a provisional rank.
2. Compute ``adp_value`` against that provisional rank and re-score.

This is stated plainly rather than hidden, because a reader's first question on
seeing both quantities is exactly how the loop is broken.

All reasons emitted here are generated from structured facts. No language model
is involved in producing a recommendation or its justification.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from fhe.core.draft.models import DraftablePlayer
from fhe.core.draft.roster import RosterNeed
from fhe.core.draft.scarcity import PositionScarcity
from fhe.core.draft.survival import survival_probability, take_now_probability
from fhe.core.draft.vorp import ReplacementBaseline, value_over_replacement
from fhe.core.types import Position, Recommendation

# --------------------------------------------------------------------------
# Weights. These define the product's opinion and are the first thing to tune.
# --------------------------------------------------------------------------
W_VORP: Final = 40.0
W_SCARCITY: Final = 12.0
# Raised from 12.0 after measuring it against a real 600-player pool. At 12 an
# unfilled starting slot barely steered the board: with two backs and two
# receivers already drafted, the top ten still held five more backs while
# quarterback and tight end sat at full need. Sweeping the weight on that same
# pool: 12 → five RB, 18 → four, 24 → three, 30 → a tight-end wall of six. So
# the useful range is 18-24 and 30 is over-corrected. 20 sits inside it and
# keeps the top of the board on value while letting an unfilled slot win the
# close calls, which is what a drafter actually wants from this signal.
W_ROSTER_NEED: Final = 20.0
W_ADP_VALUE: Final = 15.0
W_URGENCY: Final = 10.0
W_HEALTH: Final = 20.0
W_BYE_COLLISION: Final = 4.0
# Applied to kickers and defenses until the closing rounds. Without it these
# positions rank absurdly early: their projections are tightly clustered, so a
# small value-over-replacement combines with an unfilled dedicated roster slot
# and a large apparent ADP discount to put a defense in the first round. No
# human drafts that way, and the model should not either.
W_LATE_ROUND_DISCOUNT: Final = 30.0

# Positions that are only ever taken at the very end of a draft.
LATE_ROUND_POSITIONS: Final[frozenset[Position]] = frozenset({Position.K, Position.DEF})
# Spare picks allowed beyond the number of late-round slots still to fill before
# the discount lifts. One round of slack means the user is never forced into
# taking the last kicker on the board.
_LATE_ROUND_BUFFER: Final = 1

# ADP value is normalised over this many picks of disagreement. A player going
# a full two rounds later than the model ranks him is a maximal-value signal.
_ADP_VALUE_SCALE: Final = 24.0

# Players without a projection are ranked by market signal alone and capped, so
# an unknown player can never top the board on missing data.
_NO_PROJECTION_SCORE_CAP: Final = 35.0

# Recommendation thresholds.
_DRAFT_NOW_TAKE_PROBABILITY: Final = 0.55
_LIKELY_AVAILABLE_SURVIVAL: Final = 0.75
_STRONG_VALUE_ADP_DELTA: Final = 12.0
_REACH_ADP_DELTA: Final = -15.0
_AVOID_HEALTH_RISK: Final = 70.0
_DISCOUNT_HEALTH_RISK: Final = 45.0

# "Safest" and "highest upside" board slots.
_SAFE_MAX_RISK: Final = 25.0
_SAFE_MIN_CONFIDENCE: Final = 0.5
_UPSIDE_MAX_AGE: Final = 25.0
_UPSIDE_MAX_EXPERIENCE: Final = 3


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One signed, explainable contribution to a player's overall score."""

    name: str
    label: str
    points: float
    detail: str


@dataclass(frozen=True, slots=True)
class PlayerRecommendation:
    """A fully decomposed recommendation for one player."""

    player_uuid: str
    name: str
    position: Position
    team: str | None
    overall_score: float
    model_rank: int
    market_adp: float | None
    adp_value: float | None
    projected_points: float | None
    vorp: float | None
    health_risk: float | None
    availability_estimate: float | None
    next_pick_survival_probability: float | None
    take_now_probability: float | None
    tier: int | None
    recommendation: Recommendation
    components: tuple[ScoreComponent, ...]
    reasons: tuple[str, ...]
    bye_week: int | None = None

    @property
    def has_projection(self) -> bool:
        """Whether this recommendation rests on a real projection."""
        return self.projected_points is not None


@dataclass(frozen=True, slots=True)
class DraftContext:
    """Everything the engine needs to score the board once."""

    available: Sequence[DraftablePlayer]
    baseline: ReplacementBaseline
    scarcity: dict[Position, PositionScarcity]
    roster_need: RosterNeed
    current_pick: int
    next_user_pick: int | None
    roster_bye_weeks: tuple[int, ...] = field(default=())
    tier_by_player: dict[str, int] = field(default_factory=dict)
    # How many picks the user still holds, which is what makes the late-round
    # discount time-aware rather than a blanket penalty.
    user_picks_remaining: int | None = None


def _clamp01(value: float) -> float:
    """Constrain to ``[0, 1]``."""
    return max(0.0, min(1.0, value))


def _normalise_vorp(vorp: float | None, max_vorp: float) -> float:
    """Scale VORP into ``[-1, 1]`` against the best player in the pre-draft pool.

    The range is signed rather than clamped at zero. Flattening every
    sub-replacement player to 0.0 removed all ordering from the heaviest
    component, which left the ADP term deciding their relative order — and since
    that term rewards falling, the worst player at a position outscored the best
    one. A quarterback 124 points *below* the baseline outranked QB1.
    """
    if vorp is None or max_vorp <= 0:
        return 0.0
    return max(-1.0, min(1.0, vorp / max_vorp))


def _score_player(
    player: DraftablePlayer,
    context: DraftContext,
    *,
    max_vorp: float,
    provisional_rank: int | None,
) -> tuple[float, list[ScoreComponent], dict[str, float | None]]:
    """Score one player, returning the total, its components, and key metrics."""
    components: list[ScoreComponent] = []
    vorp = value_over_replacement(player, context.baseline)
    vorp_norm = _normalise_vorp(vorp, max_vorp)

    # --- projected value over replacement -------------------------------
    if vorp is not None:
        components.append(
            ScoreComponent(
                name="value_over_replacement",
                label="Value over replacement",
                points=round(W_VORP * vorp_norm, 2),
                detail=(
                    f"{abs(vorp):.1f} projected points "
                    f"{'above' if vorp >= 0 else 'below'} the "
                    f"{player.position.value}"
                    f"{context.baseline.replacement_rank.get(player.position, 0)} baseline."
                ),
            )
        )

    # --- positional scarcity ---------------------------------------------
    scarcity = context.scarcity.get(player.position)
    if scarcity is not None and scarcity.scarcity_index > 0:
        components.append(
            ScoreComponent(
                name="positional_scarcity",
                label="Positional scarcity",
                points=round(W_SCARCITY * scarcity.scarcity_index, 2),
                detail=(
                    f"{scarcity.tier_size_remaining} left in the top "
                    f"{player.position.value} tier, about "
                    f"{scarcity.expected_gone_before_next_pick:.1f} expected to go "
                    "before your next pick."
                ),
            )
        )

    # --- roster need ------------------------------------------------------
    need = context.roster_need.need_for(player.position)
    if need > 0:
        if context.roster_need.is_starter_slot_open_for(player.position):
            detail = f"Fills an open starting slot for {player.position.value}."
        else:
            detail = f"Adds depth at {player.position.value}; starters are covered."
        components.append(
            ScoreComponent(
                name="roster_need",
                label="Roster need",
                points=round(W_ROSTER_NEED * need, 2),
                detail=detail,
            )
        )

    # --- ADP value (second pass only) -------------------------------------
    #
    # Requires a projection. `adp_value` compares the market's opinion against
    # *this model's* rank, and without a projection that rank carries no
    # information about the player — it is wherever roster need and scarcity
    # happened to leave him. Crediting the difference then manufactures value
    # out of ignorance: a player nobody drafts until pick 460, ranked third
    # because nothing is known about him, scores +457 "market undervalues him"
    # and climbs the board on that alone.
    #
    # This is the same circularity the two-pass evaluation exists to break,
    # reappearing when one side of the comparison is empty. Missing data must
    # lower confidence, never invent value, so the term is omitted in both
    # directions rather than guessed at.
    adp_value: float | None = None
    if player.adp is not None and provisional_rank is not None and player.has_projection:
        adp_value = round(player.adp - provisional_rank, 1)
        normalised = _clamp01(abs(adp_value) / _ADP_VALUE_SCALE)
        signed = normalised if adp_value > 0 else -normalised
        if abs(adp_value) >= 1.0:
            direction = "later" if adp_value > 0 else "earlier"
            components.append(
                ScoreComponent(
                    name="adp_value",
                    label="ADP value",
                    points=round(W_ADP_VALUE * signed, 2),
                    detail=(
                        f"Market drafts him {abs(adp_value):.0f} picks {direction} "
                        f"than this model ranks him (ADP {player.adp:.1f} vs rank "
                        f"{provisional_rank})."
                    ),
                )
            )

    # --- next-pick urgency -------------------------------------------------
    survival = survival_probability(
        adp=player.adp,
        current_pick=context.current_pick,
        next_pick=context.next_user_pick,
        adp_stdev=player.adp_stdev,
    )
    take_now = take_now_probability(survival)
    if take_now is not None and take_now > 0 and vorp_norm > 0:
        # Urgency is scaled by value: a replacement-level player about to be
        # taken is not urgent, he is simply about to be someone else's problem.
        components.append(
            ScoreComponent(
                name="next_pick_urgency",
                label="Next-pick urgency",
                points=round(W_URGENCY * take_now * vorp_norm, 2),
                detail=(
                    f"{take_now:.0%} chance he is gone before your next pick"
                    + (f" (#{context.next_user_pick})." if context.next_user_pick else ".")
                ),
            )
        )

    # --- health adjustment --------------------------------------------------
    health_risk: float | None = None
    availability: float | None = None
    if player.health is not None:
        health_risk = player.health.risk_score
        availability = player.health.availability_estimate
        if health_risk > 0:
            penalty = -W_HEALTH * (health_risk / 100.0)
            components.append(
                ScoreComponent(
                    name="health_adjustment",
                    label="Availability risk",
                    points=round(penalty, 2),
                    detail=(
                        f"Availability risk {health_risk:.0f}/100 "
                        f"({player.health.risk_band.lower()}); estimated "
                        f"{availability:.0%} of games available."
                    ),
                )
            )

    # --- late-round position discount ----------------------------------------
    if player.position in LATE_ROUND_POSITIONS and context.user_picks_remaining is not None:
        slots_still_needed = sum(
            1 for slot in context.roster_need.unfilled_slots if slot.accepts(player.position)
        )
        spare_picks = context.user_picks_remaining - slots_still_needed
        if spare_picks > _LATE_ROUND_BUFFER:
            components.append(
                ScoreComponent(
                    name="late_round_position",
                    label="Draft timing",
                    points=-W_LATE_ROUND_DISCOUNT,
                    detail=(
                        f"{player.position.value} is a last-round pick and you still "
                        f"have {context.user_picks_remaining} selections left."
                    ),
                )
            )

    # --- bye-week collision --------------------------------------------------
    if player.bye_week is not None and context.roster_bye_weeks:
        collisions = sum(1 for w in context.roster_bye_weeks if w == player.bye_week)
        if collisions >= 2:
            components.append(
                ScoreComponent(
                    name="bye_collision",
                    label="Bye overlap",
                    points=round(-W_BYE_COLLISION * min(1.0, collisions / 4.0), 2),
                    detail=(
                        f"{collisions} rostered starters already share week {player.bye_week}."
                    ),
                )
            )

    total = round(sum(c.points for c in components), 2)
    if not player.has_projection:
        total = min(total, _NO_PROJECTION_SCORE_CAP)

    metrics: dict[str, float | None] = {
        "vorp": vorp,
        "adp_value": adp_value,
        "survival": survival,
        "take_now": take_now,
        "health_risk": health_risk,
        "availability": availability,
    }
    return total, components, metrics


def _classify(
    *,
    rank: int,
    metrics: dict[str, float | None],
    has_projection: bool,
) -> Recommendation:
    """Assign an action label from the computed metrics.

    Order matters: risk vetoes value, and urgency beats patience.
    """
    health_risk = metrics["health_risk"]
    adp_value = metrics["adp_value"]
    survival = metrics["survival"]
    take_now = metrics["take_now"]

    if health_risk is not None and health_risk >= _AVOID_HEALTH_RISK:
        return Recommendation.AVOID
    if health_risk is not None and health_risk >= _DISCOUNT_HEALTH_RISK:
        return Recommendation.DISCOUNT_RISK
    if not has_projection:
        return Recommendation.LIKELY_AVAILABLE_LATER
    if adp_value is not None and adp_value <= _REACH_ADP_DELTA:
        return Recommendation.REACH
    if rank == 1 and (take_now is None or take_now >= _DRAFT_NOW_TAKE_PROBABILITY):
        return Recommendation.DRAFT_NOW
    if adp_value is not None and adp_value >= _STRONG_VALUE_ADP_DELTA:
        return Recommendation.STRONG_VALUE
    if survival is not None and survival >= _LIKELY_AVAILABLE_SURVIVAL:
        return Recommendation.LIKELY_AVAILABLE_LATER
    if rank == 1:
        return Recommendation.DRAFT_NOW
    return Recommendation.STRONG_VALUE if rank <= 3 else Recommendation.LIKELY_AVAILABLE_LATER


def _build_reasons(
    components: Sequence[ScoreComponent],
    recommendation: Recommendation,
    metrics: dict[str, float | None],
    *,
    max_reasons: int = 4,
) -> tuple[str, ...]:
    """Turn the largest components into concise, deterministic reasons."""
    ranked = sorted(components, key=lambda c: abs(c.points), reverse=True)
    reasons = [c.detail for c in ranked[:max_reasons]]

    if recommendation is Recommendation.LIKELY_AVAILABLE_LATER:
        survival = metrics["survival"]
        if survival is not None:
            reasons.insert(0, f"{survival:.0%} chance he is still there at your next pick.")
    return tuple(reasons)


def rank_board(context: DraftContext) -> tuple[PlayerRecommendation, ...]:
    """Score and rank every available player.

    Runs the two-pass evaluation described in the module docstring and returns
    recommendations ordered best-first.
    """
    if not context.available:
        return ()

    # Fixed, pre-draft scale. See ReplacementBaseline.max_vorp for why this must
    # not be recomputed from the players still on the board.
    max_vorp = context.baseline.max_vorp
    if max_vorp <= 0:
        available_vorps = [
            v
            for v in (value_over_replacement(p, context.baseline) for p in context.available)
            if v is not None
        ]
        max_vorp = max(available_vorps) if available_vorps else 0.0

    # Pass 1: no ADP term, to obtain a provisional rank.
    provisional = sorted(
        (
            (_score_player(p, context, max_vorp=max_vorp, provisional_rank=None)[0], p)
            for p in context.available
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    provisional_rank = {
        player.player_uuid: index + 1 for index, (_, player) in enumerate(provisional)
    }

    # Pass 2: with ADP value measured against the provisional rank.
    scored: list[tuple[float, DraftablePlayer, list[ScoreComponent], dict[str, float | None]]] = []
    for player in context.available:
        total, components, metrics = _score_player(
            player,
            context,
            max_vorp=max_vorp,
            provisional_rank=provisional_rank[player.player_uuid],
        )
        scored.append((total, player, components, metrics))

    # Deterministic ordering: score desc, then ADP asc, then uuid for stability.
    scored.sort(
        key=lambda row: (-row[0], row[1].adp if row[1].adp is not None else 1e9, row[1].player_uuid)
    )

    recommendations: list[PlayerRecommendation] = []
    for index, (total, player, components, metrics) in enumerate(scored):
        rank = index + 1
        label = _classify(rank=rank, metrics=metrics, has_projection=player.has_projection)
        recommendations.append(
            PlayerRecommendation(
                player_uuid=player.player_uuid,
                name=player.name,
                position=player.position,
                team=player.team,
                overall_score=total,
                model_rank=rank,
                market_adp=player.adp,
                adp_value=metrics["adp_value"],
                projected_points=player.projected_points,
                vorp=metrics["vorp"],
                health_risk=metrics["health_risk"],
                availability_estimate=metrics["availability"],
                next_pick_survival_probability=metrics["survival"],
                take_now_probability=metrics["take_now"],
                tier=context.tier_by_player.get(player.player_uuid),
                recommendation=label,
                components=tuple(sorted(components, key=lambda c: abs(c.points), reverse=True)),
                reasons=_build_reasons(components, label, metrics),
                bye_week=player.bye_week,
            )
        )
    return tuple(recommendations)
