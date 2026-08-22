"""Sleeper onboarding.

The connection flow, in the order the user experiences it: find me, show my
leagues, show that league's drafts. Sleeper needs no credentials, so this is
three read-only lookups rather than an OAuth dance.

Nothing here starts polling. Connecting a draft is a separate, explicit step, so
a user browsing their leagues never accidentally starts hitting the provider.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query
from pydantic import Field

from fhe.api.deps import SettingsDep
from fhe.api.schemas import ApiModel
from fhe.core.errors import UnknownPlayerError
from fhe.data.providers.sleeper import SleeperProvider
from fhe.observability import get_logger

router = APIRouter(prefix="/sleeper", tags=["sleeper"])
log = get_logger(__name__)


class SleeperUserOut(ApiModel):
    """A resolved Sleeper account."""

    user_id: str
    username: str | None = None
    display_name: str | None = None


class SleeperLeagueOut(ApiModel):
    """A league the user belongs to."""

    league_id: str
    name: str
    season: str
    status: str
    total_rosters: int
    scoring_format: str
    roster_positions: list[str]
    draft_id: str | None = None
    is_superflex: bool = False


class SleeperDraftOut(ApiModel):
    """A draft belonging to a league."""

    draft_id: str
    league_id: str | None = None
    status: str
    draft_type: str
    season: str
    team_count: int | None = None
    rounds: int | None = None
    scoring_type: str | None = None
    start_time_ms: int | None = None
    user_draft_slot: int | None = Field(
        default=None, description="The seat this user occupies, when it can be resolved."
    )


class NflStateOut(ApiModel):
    """Current NFL season state, used to pick the right season by default."""

    season: str
    season_type: str
    week: int


def _scoring_format(scoring_settings: dict[str, float]) -> str:
    """Infer the reception-scoring family from a league's scoring settings.

    Sleeper does not publish a format label on the league object; it publishes
    the actual points-per-reception, which is the more truthful thing to read.
    """
    reception = float(scoring_settings.get("rec", 0.0) or 0.0)
    if reception >= 0.75:
        return "ppr"
    if reception >= 0.25:
        return "half_ppr"
    return "standard"


@router.get("/state", response_model=NflStateOut, summary="Current NFL state")
async def nfl_state(settings: SettingsDep) -> NflStateOut:
    """The current season and week, so the client need not guess."""
    async with SleeperProvider(settings) as sleeper:
        state = await sleeper.get_nfl_state()
    if state is None:
        raise UnknownPlayerError("Sleeper did not return NFL state")
    return NflStateOut(season=state.season, season_type=state.season_type, week=state.week)


@router.get("/users/{username}", response_model=SleeperUserOut | None, summary="Look up a user")
async def get_user(
    settings: SettingsDep,
    username: Annotated[str, Path(min_length=1, max_length=64)],
) -> SleeperUserOut | None:
    """Resolve a Sleeper username to an account.

    Returns ``null`` for an unknown username rather than an error: Sleeper
    answers HTTP 200 with a null body, and "no such user" is an ordinary
    onboarding outcome, not a failure.
    """
    async with SleeperProvider(settings) as sleeper:
        user = await sleeper.get_user(username)
    if user is None:
        return None
    return SleeperUserOut(
        user_id=user.user_id, username=user.username, display_name=user.display_name
    )


@router.get(
    "/users/{user_id}/leagues",
    response_model=list[SleeperLeagueOut],
    summary="Leagues for a user",
)
async def get_leagues(
    settings: SettingsDep,
    user_id: Annotated[str, Path(min_length=1, max_length=64)],
    season: Annotated[str | None, Query(description="Defaults to the current season.")] = None,
) -> list[SleeperLeagueOut]:
    """Every NFL league the user belongs to in a season."""
    async with SleeperProvider(settings) as sleeper:
        resolved_season = season
        if resolved_season is None:
            state = await sleeper.get_nfl_state()
            resolved_season = state.season if state else ""
        leagues = await sleeper.get_leagues(user_id, resolved_season)

    return [
        SleeperLeagueOut(
            league_id=league.league_id,
            name=league.name,
            season=league.season,
            status=league.status,
            total_rosters=league.total_rosters,
            scoring_format=_scoring_format(league.scoring_settings),
            roster_positions=list(league.roster_positions),
            draft_id=league.draft_id,
            is_superflex="SUPER_FLEX" in league.roster_positions,
        )
        for league in leagues
    ]


@router.get(
    "/leagues/{league_id}/drafts",
    response_model=list[SleeperDraftOut],
    summary="Drafts for a league",
)
async def get_drafts(
    settings: SettingsDep,
    league_id: Annotated[str, Path(min_length=1, max_length=64)],
    user_id: Annotated[
        str | None,
        Query(description="Resolves which seat belongs to this user."),
    ] = None,
) -> list[SleeperDraftOut]:
    """Drafts belonging to a league, with the user's seat when it can be resolved.

    The seat comes from the draft's own ``draft_order`` map, which is the only
    authoritative statement of who sits where; inferring it from roster order
    would be a guess that silently breaks on a traded pick.
    """
    async with SleeperProvider(settings) as sleeper:
        drafts = await sleeper.get_league_drafts(league_id)

    return [
        SleeperDraftOut(
            draft_id=draft.draft_id,
            league_id=draft.league_id,
            status=draft.status,
            draft_type=draft.draft_type,
            season=draft.season,
            team_count=draft.team_count,
            rounds=draft.rounds,
            scoring_type=draft.scoring_type,
            start_time_ms=draft.start_time_ms,
            user_draft_slot=(
                draft.draft_order.get(user_id) if user_id and draft.draft_order else None
            ),
        )
        for draft in drafts
    ]
