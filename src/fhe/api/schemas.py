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


class InjurySpellOut(ApiModel):
    """One distinct injury, reconstructed from the weekly reports that named it.

    Sent alongside the raw reports rather than derived in the browser: how many
    injuries a player has had is a domain judgement, and counting rows in
    TypeScript is what made the drawer label a single nine-week absence as a
    recurring problem.
    """

    body_region: str
    first_season: int
    last_season: int
    first_week: int | None = None
    last_week: int | None = None
    reports: int
    weeks_absent: int
    worst_designation: str
    raw_descriptors: list[str] = Field(default_factory=list)
    recurrence_class: str


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


class WorkloadOut(ApiModel):
    """Recent usage, which drives both exposure and durability signals."""

    season: int | None = None
    games_played: int | None = None
    snaps_per_game: float | None = None
    carries_per_game: float | None = None
    targets_per_game: float | None = None
    touches_per_game: float | None = None


class UsageOut(ApiModel):
    """Measured opportunity and scoring volatility from the last played season.

    Distinct from `WorkloadOut`, which serves the health model's exposure terms.
    These answer whether a projection is corroborated, and how steady the
    scoring behind it was. Every field is nullable because a player without a
    measured season — a rookie, most obviously — is unknown rather than zero.
    """

    season: int | None = None
    games_sampled: int | None = None
    snap_share: float | None = Field(
        default=None, description="Mean share of offensive snaps, 0-1."
    )
    touches_per_game: float | None = None
    points_per_game: float | None = None
    points_stdev: float | None = Field(
        default=None, description="Week-to-week standard deviation of fantasy points."
    )
    volatility: float | None = Field(
        default=None,
        description=(
            "Spread relative to the mean. Absent below a low scoring rate, where "
            "the ratio is a denominator artefact rather than boom-or-bust."
        ),
    )


class PlayoffScheduleOut(ApiModel):
    """Fantasy-playoff matchup difficulty for this player's position."""

    weeks_covered: int = 0
    opponents: list[str] = Field(default_factory=list)
    points_allowed_per_game: float | None = None
    league_average: float | None = None
    difficulty: float | None = Field(
        default=None,
        description=(
            "Ratio to the league average. Above 1.0 is a favourable draw, below "
            "1.0 a hard one. Absent when too few matchups are known."
        ),
    )


class RookieOpportunityOut(ApiModel):
    """How willing a rookie's team has been to play rookies, under this coach."""

    team: str
    coach: str | None = None
    seasons_under_coach: int = 0
    average_rookie_touches: float | None = None
    rank: int | None = None
    teams_ranked: int = 0
    had_recent_workhorse: bool = False
    boost: float = 0.0


class PlayerDetail(PlayerSummary):
    """Full player record for the detail drawer."""

    jersey_number: int | None = None
    height_inches: int | None = None
    weight_pounds: int | None = None
    college: str | None = None
    identity_method: str | None = None
    identity_confidence: float | None = None
    external_ids: dict[str, str] = Field(default_factory=dict)
    health: HealthOut | None = None
    injury_history: list[InjuryEventOut] = Field(default_factory=list)
    injury_spells: list[InjurySpellOut] = Field(default_factory=list)
    workload: WorkloadOut | None = None
    usage: UsageOut | None = None
    playoff_schedule: PlayoffScheduleOut | None = None
    is_rookie: bool = False
    rookie_opportunity: RookieOpportunityOut | None = None
    projected_points: float | None = None
    market_adp: float | None = None
    adp_stdev: float | None = None
    projection_source: str | None = None
    adp_source: str | None = None
    is_demo: bool = False


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


class PlayerPage(ApiModel):
    """One page of players, for screens that are not scoped to a draft."""

    total: int = Field(description="Players matching the filters, before paging.")
    offset: int
    limit: int
    players: list[PlayerDetail] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Gaps in the underlying data that limit what these records can say.",
    )
    environment: str


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
    warnings: list[str] = Field(
        default_factory=list,
        description="Gaps in the underlying data that limit what this board can say.",
    )


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


class PollerStatusOut(ApiModel):
    """Health of the live poller following a draft."""

    state: str
    poll_count: int
    picks_observed: int
    consecutive_failures: int
    is_stale: bool = Field(
        description="True once repeated failures mean the feed can no longer be trusted."
    )
    last_success_at: datetime | None = None
    last_error: str | None = None
    seconds_since_success: float | None = None


class DraftStateOut(ApiModel):
    """Lifecycle summary of a draft, live or simulated."""

    draft_id: str
    is_demo: bool
    status: str
    pick_count: int
    total_picks: int
    current_pick: int | None = None
    is_complete: bool
    is_user_on_the_clock: bool
    user_draft_slot: int | None = None
    created_at: datetime
    poller: PollerStatusOut | None = Field(
        default=None, description="Present only for a live provider draft."
    )


class ConnectDraftRequest(ApiModel):
    """Request body for connecting a real Sleeper draft."""

    league_id: str = Field(min_length=1, max_length=64)
    draft_id: str = Field(min_length=1, max_length=64)
    user_id: str | None = Field(
        default=None,
        description="Resolves which seat is yours, from the draft's own order map.",
    )
    follow: bool = Field(
        default=True,
        description="Start polling for new picks. Off for reviewing a finished draft.",
    )


class PoolProvenanceOut(ApiModel):
    """How complete the player pool behind a live draft is."""

    player_count: int
    with_projection: int
    with_adp: int
    with_health: int
    projection_sources: list[str] = Field(default_factory=list)
    adp_sources: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Gaps a user should know about before trusting the board.",
    )


class ConnectedDraftOut(ApiModel):
    """Result of connecting a live draft."""

    draft_id: str
    league_id: str
    league_name: str
    status: str
    user_draft_slot: int | None = None
    picks_already_made: int
    following: bool
    league: LeagueSettingsOut
    pool: PoolProvenanceOut


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
