"""Player reads that are not scoped to a draft.

Everything the war room shows is reachable through ``/drafts/{id}/…`` because a
board is always evaluated in a league's context. That is the right shape for
drafting, and the wrong shape for the two screens that are not about a draft at
all: a rankings page and a health centre.

These endpoints exist so those pages do not have to invent a draft to read a
player. They are strictly read-only, they compute no recommendation — ranking a
player against a league is the draft engine's job and stays there — and they
return the same ``PlayerDetail`` contract the drawer already renders, so a
component written for one works in the other.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from fhe.api.deps import SessionDep, SettingsDep
from fhe.api.mappers import player_detail
from fhe.api.schemas import PlayerDetail, PlayerPage
from fhe.api.services.player_pool import load_player_pool
from fhe.core.draft.models import DraftablePlayer
from fhe.core.errors import UnknownPlayerError
from fhe.core.types import Position, ScoringFormat
from fhe.db.base import utcnow

router = APIRouter(prefix="/players", tags=["players"])

# A page of players. Large enough that a rankings table can render a full
# position without paging, small enough that one request cannot be turned into
# arbitrary work.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _matches(player: DraftablePlayer, position: Position | None, query: str | None) -> bool:
    """Whether a player passes the requested filters."""
    if position is not None and player.position is not position:
        return False
    return not (query and query.lower() not in player.name.lower())


@router.get("", response_model=PlayerPage, summary="Browse players")
async def list_players(
    session: SessionDep,
    settings: SettingsDep,
    season: Annotated[int | None, Query(description="Season to value players for.")] = None,
    scoring_format: Annotated[str, Query(description="Reception-scoring family.")] = "ppr",
    position: Annotated[str | None, Query(description="Filter to one position.")] = None,
    query: Annotated[str | None, Query(description="Case-insensitive name search.")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PlayerPage:
    """A page of players with their health assessment and projection.

    Ordered by market popularity, which is the order a person browsing expects
    and is deliberately *not* a recommendation — ranking is league-specific and
    belongs to the draft engine.
    """
    as_of = utcnow().date()
    pool, provenance = await load_player_pool(
        session,
        season=season if season is not None else _default_season(as_of),
        scoring_format=ScoringFormat.parse(scoring_format),
        as_of=as_of,
    )

    wanted = Position.parse(position) if position else None
    filtered = [player for player in pool if _matches(player, wanted, query)]
    page = filtered[offset : offset + limit]

    return PlayerPage(
        total=len(filtered),
        offset=offset,
        limit=limit,
        players=[player_detail(player, is_demo=False) for player in page],
        warnings=list(provenance.warnings),
        environment=settings.env,
    )


@router.get("/{player_uuid}", response_model=PlayerDetail, summary="Player detail")
async def get_player(
    player_uuid: str,
    session: SessionDep,
    season: Annotated[int | None, Query(description="Season to value the player for.")] = None,
    scoring_format: Annotated[str, Query(description="Reception-scoring family.")] = "ppr",
) -> PlayerDetail:
    """One player's full record, outside any draft.

    The same payload the war-room drawer renders, so the detail view is one
    component rather than two that drift apart.
    """
    as_of = utcnow().date()
    pool, _ = await load_player_pool(
        session,
        season=season if season is not None else _default_season(as_of),
        scoring_format=ScoringFormat.parse(scoring_format),
        as_of=as_of,
    )
    for player in pool:
        if player.player_uuid == player_uuid:
            return player_detail(player, is_demo=False)
    raise UnknownPlayerError(f"no player {player_uuid!r}")


def _default_season(today: date) -> int:
    """The season a date belongs to.

    The NFL season is named for the calendar year it starts in, so anything
    before March belongs to the previous year's season rather than the new one.
    """
    return today.year if today.month >= 3 else today.year - 1
