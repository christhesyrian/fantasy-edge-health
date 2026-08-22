"""API response contracts.

These are the boundary types. They are deliberately separate from the domain
dataclasses in :mod:`fhe.core`: a domain refactor should not silently reshape a
public payload, and a wire format should not constrain the domain model.

Every metric that can be stale carries its source and observation time, because
the product's promise is that a number on screen can always say where it came
from and how old it is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base for every response model."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


# ---------------------------------------------------------------- provenance


class Provenance(ApiModel):
    """Where a value came from and when it was observed."""

    source: str = Field(description="Provider or importer that supplied the value.")
    observed_at: datetime | None = Field(
        default=None, description="When the fact was true, per the source."
    )
    freshness: str = Field(default="FRESH", description="LIVE, FRESH, STALE, EXPIRED, or MISSING.")


# -------------------------------------------------------------------- health


class RiskComponentOut(ApiModel):
    """One explainable contribution to an availability-risk score."""

    name: str
    label: str
    points: float
    detail: str


class HealthOut(ApiModel):
    """A player's availability-risk assessment.

    ``risk_score`` is an availability estimate, never a medical claim. The
    ``limitations`` list is part of the contract and is rendered in the UI.
    """

    risk_score: float = Field(ge=0, le=100)
    raw_score: float
    risk_band: str
    availability_estimate: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    practice_trajectory: str
    model_version: str
    components: list[RiskComponentOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    designation: str | None = None
    body_region: str | None = None
    raw_body_part: str | None = Field(
        default=None, description="The provider's original wording, never discarded."
    )


class InjuryEventOut(ApiModel):
    """One historical injury observation."""

    season: int
    week: int | None
    body_region: str
    raw_descriptor: str | None
    designation: str
    games_missed: int | None = None
    observed_at: datetime | None = None


# ------------------------------------------------------------------- players


class PlayerSummary(ApiModel):
    """Compact player record for tables and search."""

    player_uuid: str
    name: str
    position: str
    team: str | None = None
    age: float | None = None
    years_experience: int | None = None
    bye_week: int | None = None


class PlayerDetail(PlayerSummary):
    """Full player record for the detail drawer."""

    jersey_number: int | None = None
    height_inches: int | None = None
    weight_pounds: int | None = None
    college: str | None = None
    identity_method: str
    identity_confidence: float
    external_ids: dict[str, str] = Field(default_factory=dict)
    health: HealthOut | None = None
    injury_history: list[InjuryEventOut] = Field(default_factory=list)


# ------------------------------------------------------------- draft scoring


class ScoreComponentOut(ApiModel):
    """One signed contribution to an overall draft score."""

    name: str
    label: str
    points: float
    detail: str


class RecommendationOut(ApiModel):
    """A fully decomposed recommendation for one player.

    ``components`` always sums to ``overall_score``. The UI relies on that to
    render the arithmetic behind the headline number.
    """

    player_uuid: str
    name: str
    position: str
    team: str | None = None

    overall_score: float
    model_rank: int
    recommendation: str

    market_adp: float | None = None
    adp_value: float | None = None
    projected_points: float | None = None
    vorp: float | None = None
    tier: int | None = None

    health_risk: float | None = None
    availability_estimate: float | None = None
    next_pick_survival_probability: float | None = None
    take_now_probability: float | None = None
    bye_week: int | None = None

    components: list[ScoreComponentOut] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class PositionScarcityOut(ApiModel):
    """How fast usable talent is disappearing at one position."""

    position: str
    available_starters: int
    tier_size_remaining: int
    next_tier_dropoff: float | None = None
    expected_gone_before_next_pick: float
    scarcity_index: float


class DraftAlertOut(ApiModel):
    """An actionable notice for the war room."""

    key: str
    level: str
    message: str
    position: str | None = None
    player_uuid: str | None = None


class RosterSlotOut(ApiModel):
    """One lineup slot and its occupant, if any."""

    slot: str
    is_starter: bool
    player: PlayerSummary | None = None


class TeamRosterOut(ApiModel):
    """A fantasy team's roster, arranged by lineup slot."""

    draft_slot: int
    roster_id: int | None = None
    display_name: str | None = None
    is_user: bool = False
    lineup: list[RosterSlotOut] = Field(default_factory=list)
    bench: list[PlayerSummary] = Field(default_factory=list)
    unfilled_starting_slots: list[str] = Field(default_factory=list)


class DraftPickOut(ApiModel):
    """A completed selection."""

    pick_no: int
    round_number: int
    draft_slot: int
    roster_id: int | None = None
    player: PlayerSummary | None = None
    is_keeper: bool = False


class LeagueSettingsOut(ApiModel):
    """The league shape the engine is reasoning about."""

    team_count: int
    scoring_format: str
    draft_type: str
    rounds: int
    roster_positions: list[str]
    user_draft_slot: int | None = None
    is_superflex: bool = False
    replacement_ranks: dict[str, int] = Field(default_factory=dict)


class DraftBoardOut(ApiModel):
    """The complete war-room view for one moment in a draft."""

    draft_id: str
    is_demo: bool = Field(description="True when this board is driven by synthetic data.")
    status: str
    current_pick: int | None = None
    current_round: int | None = None
    next_user_pick: int | None = None
    picks_until_user_turn: int | None = None
    is_user_on_the_clock: bool = False

    league: LeagueSettingsOut
    recommendations: list[RecommendationOut] = Field(default_factory=list)
    best_pick: RecommendationOut | None = None
    safest_pick: RecommendationOut | None = None
    highest_upside: RecommendationOut | None = None
    best_value: RecommendationOut | None = None

    scarcity: list[PositionScarcityOut] = Field(default_factory=list)
    alerts: list[DraftAlertOut] = Field(default_factory=list)
    my_roster: TeamRosterOut | None = None
    recent_picks: list[DraftPickOut] = Field(default_factory=list)

    computed_at: datetime
    computation_ms: float | None = None
    provenance: list[Provenance] = Field(default_factory=list)


# --------------------------------------------------------------- simulations


class SimulationCreate(ApiModel):
    """Request body for starting a mock draft."""

    team_count: int = Field(default=12, ge=2, le=32)
    scoring_format: str = Field(default="ppr")
    draft_type: str = Field(default="snake")
    roster_positions: list[str] | None = Field(
        default=None,
        description="Defaults to a conventional QB/2RB/2WR/TE/FLEX/K/DEF lineup.",
    )
    user_draft_slot: int = Field(default=5, ge=1, le=32)
    seed: int = Field(default=42, description="Same seed reproduces the same draft.")
    temperature: float = Field(
        default=3.0,
        ge=0.1,
        le=20.0,
        description="Higher makes the computer teams draft more chaotically.",
    )


class SimulationState(ApiModel):
    """Lifecycle summary of a simulation."""

    simulation_id: str
    is_demo: bool = True
    seed: int
    status: str
    pick_count: int
    total_picks: int
    is_complete: bool
    is_user_on_the_clock: bool
    created_at: datetime


class AdvanceRequest(ApiModel):
    """How many computer picks to make."""

    picks: int = Field(
        default=1,
        ge=1,
        le=500,
        description="Use a large value with stop_at_user_turn to fast-forward.",
    )
    stop_at_user_turn: bool = True


class PickRequest(ApiModel):
    """The user's selection."""

    player_uuid: str


# --------------------------------------------------------------- csv uploads


class ImportResult(ApiModel):
    """Outcome of a CSV import."""

    dataset: str
    source: str
    status: str
    rows_read: int
    rows_written: int
    rows_rejected: int
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    rejection_samples: list[dict[str, Any]] = Field(default_factory=list)


# --------------------------------------------------------------- diagnostics


class IngestionRunOut(ApiModel):
    """One ingestion run's lineage."""

    id: int
    provider: str
    dataset: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    rows_read: int
    rows_written: int
    rows_rejected: int
    rows_unresolved_identity: int
    error_category: str | None = None


class PipelineHealth(ApiModel):
    """Recent pipeline health for the developer diagnostics page."""

    recent_runs: list[IngestionRunOut] = Field(default_factory=list)
    unresolved_identity_conflicts: int = 0
    players_tracked: int = 0
    failing_checks: int = 0


# ------------------------------------------------------------------- service


class HealthStatus(ApiModel):
    """Liveness or readiness result."""

    status: str
    version: str
    environment: str
    checks: dict[str, str] = Field(default_factory=dict)
    degradations: list[str] = Field(
        default_factory=list,
        description="Active fallbacks, so a degraded setup is never mistaken for production.",
    )


class ErrorResponse(ApiModel):
    """Uniform error body."""

    error: str
    detail: str
    request_id: str | None = None
