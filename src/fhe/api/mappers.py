"""Translation from domain objects to API contracts.

Kept in one module so the wire format has a single, reviewable definition, and
so a change to a domain dataclass produces a type error here rather than a
silently reshaped payload.
"""

from __future__ import annotations

from fhe.api.schemas import (
    DraftAlertOut,
    DraftPickOut,
    HealthOut,
    InjuryEventOut,
    InjurySpellOut,
    LeagueSettingsOut,
    PlayerDetail,
    PlayerSummary,
    PlayoffScheduleOut,
    PositionScarcityOut,
    RecommendationOut,
    RiskComponentOut,
    RookieOpportunityOut,
    RosterSlotOut,
    ScoreComponentOut,
    TeamRosterOut,
    UsageOut,
    WorkloadOut,
)
from fhe.core.draft.board import DraftAlert
from fhe.core.draft.engine import PlayerRecommendation
from fhe.core.draft.models import DraftablePlayer, DraftPick
from fhe.core.draft.roster import compute_roster_need
from fhe.core.draft.scarcity import PositionScarcity
from fhe.core.health.models import HealthAssessment
from fhe.core.health.spells import collapse_to_spells
from fhe.core.league import LeagueSettings
from fhe.core.types import SLOT_ELIGIBILITY, Position, RosterSlot


def player_summary(player: DraftablePlayer) -> PlayerSummary:
    """Map a draftable player to its compact wire form."""
    return PlayerSummary(
        player_uuid=player.player_uuid,
        name=player.name,
        position=player.position.value,
        team=player.team,
        age=player.age,
        years_experience=player.years_experience,
        bye_week=player.bye_week,
    )


def health_out(assessment: HealthAssessment) -> HealthOut:
    """Map a health assessment, preserving its limitations."""
    return HealthOut(
        risk_score=assessment.risk_score,
        raw_score=assessment.raw_score,
        risk_band=assessment.risk_band,
        availability_estimate=assessment.availability_estimate,
        confidence=assessment.confidence,
        practice_trajectory=assessment.practice_trajectory.value,
        model_version=assessment.model_version,
        components=[
            RiskComponentOut(name=c.name, label=c.label, points=c.points, detail=c.detail)
            for c in assessment.components
        ],
        limitations=list(assessment.limitations),
    )


def player_detail(player: DraftablePlayer, *, is_demo: bool) -> PlayerDetail:
    """Map a draftable player to the full drawer payload.

    Injury history is returned newest-first so a timeline renders in the order a
    reader scans it, and the raw provider descriptor rides along with every
    normalised region.
    """
    workload = player.workload
    usage = player.usage
    playoff = player.playoff_schedule
    landing = player.rookie_opportunity
    return PlayerDetail(
        player_uuid=player.player_uuid,
        name=player.name,
        position=player.position.value,
        team=player.team,
        age=player.age,
        years_experience=player.years_experience,
        bye_week=player.bye_week,
        health=health_out(player.health) if player.health else None,
        injury_history=[
            InjuryEventOut(
                season=event.season,
                week=event.week,
                body_region=event.region.value,
                raw_descriptor=event.raw_descriptor,
                designation=event.designation.value,
                games_missed=event.games_missed,
            )
            for event in sorted(
                player.injury_history,
                key=lambda e: (e.season, e.week or 0),
                reverse=True,
            )
        ],
        injury_spells=[
            InjurySpellOut(
                body_region=spell.region.value,
                first_season=spell.first_season,
                last_season=spell.last_season,
                first_week=spell.first_week,
                last_week=spell.last_week,
                reports=spell.reports,
                weeks_absent=spell.missed_weeks,
                worst_designation=spell.worst_designation.value,
                raw_descriptors=list(spell.raw_descriptors),
                recurrence_class=spell.region.recurrence_class.value,
            )
            for spell in sorted(
                collapse_to_spells(player.injury_history),
                key=lambda s: (s.first_season, s.first_week or 0),
                reverse=True,
            )
        ],
        is_rookie=player.is_rookie,
        rookie_opportunity=(
            RookieOpportunityOut(
                team=landing.team,
                coach=landing.coach,
                seasons_under_coach=landing.seasons_under_coach,
                average_rookie_touches=landing.average_rookie_touches,
                rank=landing.rank,
                teams_ranked=landing.teams_ranked,
                had_recent_workhorse=landing.had_recent_workhorse,
                boost=landing.boost,
            )
            if landing
            else None
        ),
        playoff_schedule=(
            PlayoffScheduleOut(
                weeks_covered=playoff.weeks_covered,
                opponents=list(playoff.opponents),
                points_allowed_per_game=playoff.points_allowed_per_game,
                league_average=playoff.league_average,
                difficulty=round(playoff.difficulty, 3) if playoff.difficulty is not None else None,
            )
            if playoff
            else None
        ),
        usage=(
            UsageOut(
                season=usage.season,
                games_sampled=usage.games_sampled,
                snap_share=usage.snap_share,
                touches_per_game=usage.touches_per_game,
                points_per_game=usage.points_per_game,
                points_stdev=usage.points_stdev,
                volatility=round(usage.volatility, 2) if usage.volatility is not None else None,
            )
            if usage
            else None
        ),
        workload=(
            WorkloadOut(
                season=workload.season,
                games_played=workload.games_played,
                snaps_per_game=workload.snaps_per_game,
                carries_per_game=workload.carries_per_game,
                targets_per_game=workload.targets_per_game,
                touches_per_game=workload.touches_per_game,
            )
            if workload
            else None
        ),
        projected_points=player.projected_points,
        market_adp=player.adp,
        adp_stdev=player.adp_stdev,
        projection_source=player.projection_source,
        adp_source=player.adp_source,
        is_demo=is_demo,
    )


def recommendation_out(recommendation: PlayerRecommendation) -> RecommendationOut:
    """Map a recommendation, including the components that justify its score."""
    return RecommendationOut(
        player_uuid=recommendation.player_uuid,
        name=recommendation.name,
        position=recommendation.position.value,
        team=recommendation.team,
        overall_score=recommendation.overall_score,
        model_rank=recommendation.model_rank,
        recommendation=recommendation.recommendation.value,
        market_adp=recommendation.market_adp,
        adp_value=recommendation.adp_value,
        projected_points=recommendation.projected_points,
        vorp=recommendation.vorp,
        tier=recommendation.tier,
        health_risk=recommendation.health_risk,
        availability_estimate=recommendation.availability_estimate,
        next_pick_survival_probability=recommendation.next_pick_survival_probability,
        take_now_probability=recommendation.take_now_probability,
        bye_week=recommendation.bye_week,
        components=[
            ScoreComponentOut(name=c.name, label=c.label, points=c.points, detail=c.detail)
            for c in recommendation.components
        ],
        reasons=list(recommendation.reasons),
    )


def scarcity_out(scarcity: PositionScarcity) -> PositionScarcityOut:
    """Map a positional scarcity summary."""
    return PositionScarcityOut(
        position=scarcity.position.value,
        available_starters=scarcity.available_starters,
        tier_size_remaining=scarcity.tier_size_remaining,
        next_tier_dropoff=scarcity.next_tier_dropoff,
        expected_gone_before_next_pick=scarcity.expected_gone_before_next_pick,
        scarcity_index=scarcity.scarcity_index,
    )


def alert_out(alert: DraftAlert) -> DraftAlertOut:
    """Map a war-room alert."""
    return DraftAlertOut(
        key=alert.key,
        level=alert.level.value,
        message=alert.message,
        position=alert.position.value if alert.position else None,
        player_uuid=alert.player_uuid,
    )


def league_out(league: LeagueSettings) -> LeagueSettingsOut:
    """Map league configuration, including the replacement ranks it implies."""
    return LeagueSettingsOut(
        team_count=league.team_count,
        scoring_format=league.scoring_format.value,
        draft_type=league.draft_type.value,
        rounds=league.total_rounds,
        roster_positions=[slot.value for slot in league.roster_slots],
        user_draft_slot=league.user_draft_slot,
        is_superflex=league.is_superflex,
        replacement_ranks={
            position.value: rank for position, rank in league.replacement_rank.items()
        },
    )


def pick_out(pick: DraftPick, players: dict[str, DraftablePlayer]) -> DraftPickOut:
    """Map a completed selection, resolving the player when it is known."""
    player = players.get(pick.player_uuid)
    return DraftPickOut(
        pick_no=pick.pick_no,
        round_number=pick.round_number,
        draft_slot=pick.draft_slot,
        roster_id=pick.roster_id,
        player=player_summary(player) if player else None,
        is_keeper=pick.is_keeper,
    )


# Fill order matching :mod:`fhe.core.draft.roster`: most restrictive slot first,
# so a flex is never consumed by the only candidate for a dedicated slot.
_SLOT_FILL_PRIORITY: dict[RosterSlot, int] = {
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


def roster_out(
    league: LeagueSettings,
    draft_slot: int,
    player_uuids: tuple[str, ...],
    players: dict[str, DraftablePlayer],
    *,
    roster_id: int | None = None,
    display_name: str | None = None,
    is_user: bool = False,
) -> TeamRosterOut:
    """Arrange a roster into starting slots plus a bench.

    Assignment mirrors the roster-need calculation exactly, so the lineup the
    user sees is the same one the engine reasoned about. A mismatch here would
    make the engine look wrong even when it was right.
    """
    roster_players = [players[u] for u in player_uuids if u in players]
    # Best players fill starting slots first, which is how a manager would set
    # the lineup, and keeps the bench genuinely the leftovers.
    remaining = sorted(roster_players, key=lambda p: p.projected_points or 0.0, reverse=True)

    lineup: list[RosterSlotOut] = []
    unfilled: list[str] = []
    ordered_slots = sorted(league.starting_slots, key=lambda s: _SLOT_FILL_PRIORITY.get(s, 9))

    for slot in ordered_slots:
        eligible: frozenset[Position] = SLOT_ELIGIBILITY.get(slot, frozenset())
        chosen: DraftablePlayer | None = None
        for candidate in remaining:
            if candidate.position in eligible:
                chosen = candidate
                break
        if chosen is not None:
            remaining.remove(chosen)
            lineup.append(
                RosterSlotOut(slot=slot.value, is_starter=True, player=player_summary(chosen))
            )
        else:
            unfilled.append(slot.value)
            lineup.append(RosterSlotOut(slot=slot.value, is_starter=True, player=None))

    need = compute_roster_need(league, [p.position for p in roster_players])

    return TeamRosterOut(
        draft_slot=draft_slot,
        roster_id=roster_id,
        display_name=display_name,
        is_user=is_user,
        lineup=lineup,
        bench=[player_summary(p) for p in remaining],
        unfilled_starting_slots=unfilled or [s.value for s in need.unfilled_slots],
    )
