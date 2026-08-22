"""Mock draft endpoints.

This is the demo experience: a reviewer with no Sleeper account, no credentials,
and no ingested data can start a draft here and watch the war room work.

The simulation drives the same engine the live draft uses, so it is a genuine
rehearsal rather than a parallel implementation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from fhe.api.deps import DemoPoolDep, RegistryDep
from fhe.api.events import DraftEvent, EventType
from fhe.api.mappers import pick_out, player_detail
from fhe.api.schemas import (
    AdvanceRequest,
    DraftBoardOut,
    DraftPickOut,
    PickRequest,
    PlayerDetail,
    SimulationCreate,
    SimulationState,
)
from fhe.api.services.board_builder import DEFAULT_BOARD_DEPTH, build_board_payload
from fhe.api.services.draft_session import DraftSession
from fhe.core.errors import UnknownPlayerError
from fhe.core.league import LeagueSettings
from fhe.core.types import DraftType, ScoringFormat
from fhe.observability import get_logger

router = APIRouter(prefix="/simulations", tags=["simulations"])
log = get_logger(__name__)

DEFAULT_ROSTER_POSITIONS = [
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "K",
    "DEF",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
]

# Keep-alive cadence for the event stream. Proxies commonly drop an idle
# connection at 60s, and a paused draft is legitimately quiet for longer.
HEARTBEAT_SECONDS = 15.0


def _state_of(session: DraftSession) -> SimulationState:
    """Summarise a session's lifecycle."""
    state = session.draft_state
    return SimulationState(
        simulation_id=session.session_id,
        is_demo=session.is_demo,
        seed=session.seed or 0,
        status="complete" if state.is_complete else "drafting",
        pick_count=state.pick_count,
        total_picks=session.league.total_picks,
        is_complete=state.is_complete,
        is_user_on_the_clock=session.is_user_on_the_clock,
        created_at=session.created_at,
    )


@router.post(
    "",
    response_model=SimulationState,
    status_code=status.HTTP_201_CREATED,
    summary="Start a mock draft",
)
async def create_simulation(
    body: SimulationCreate, registry: RegistryDep, pool: DemoPoolDep
) -> SimulationState:
    """Create a seeded mock draft against the synthetic demo pool.

    The same ``seed`` always reproduces the same draft, which is what makes a
    simulation reusable as a regression test rather than only a demo.
    """
    league = LeagueSettings.from_tokens(
        team_count=body.team_count,
        roster_position_tokens=body.roster_positions or DEFAULT_ROSTER_POSITIONS,
        scoring_format=ScoringFormat.parse(body.scoring_format),
        draft_type=DraftType.parse(body.draft_type),
        user_draft_slot=min(body.user_draft_slot, body.team_count),
    )
    session = await registry.create_simulation(
        league, pool, seed=body.seed, temperature=body.temperature
    )
    session.evaluate()
    return _state_of(session)


@router.get("/{simulation_id}", response_model=SimulationState, summary="Simulation state")
async def get_simulation(simulation_id: str, registry: RegistryDep) -> SimulationState:
    """Fetch a simulation's lifecycle summary."""
    return _state_of(registry.get(simulation_id))


@router.get("/{simulation_id}/board", response_model=DraftBoardOut, summary="Current draft board")
async def get_board(
    simulation_id: str,
    registry: RegistryDep,
    depth: int = Query(
        default=DEFAULT_BOARD_DEPTH,
        ge=1,
        le=500,
        description="How many ranked players to return.",
    ),
) -> DraftBoardOut:
    """Return the complete war-room view.

    This is also the canonical-state endpoint a client re-reads after a dropped
    event stream, rather than trying to replay the gap.
    """
    session = registry.get(simulation_id)
    session.evaluate()
    return build_board_payload(session, depth=depth)


@router.get(
    "/{simulation_id}/players/{player_uuid}",
    response_model=PlayerDetail,
    summary="Player detail",
)
async def get_player(simulation_id: str, player_uuid: str, registry: RegistryDep) -> PlayerDetail:
    """Everything the player drawer needs, without leaving draft context."""
    session = registry.get(simulation_id)
    player = session.players_by_uuid.get(player_uuid)
    if player is None:
        raise UnknownPlayerError(f"no player {player_uuid!r} in this draft pool")
    return player_detail(player, is_demo=session.is_demo)


@router.get(
    "/{simulation_id}/compare",
    response_model=list[PlayerDetail],
    summary="Compare players",
)
async def compare_players(
    simulation_id: str,
    registry: RegistryDep,
    player_uuid: Annotated[
        list[str],
        Query(
            min_length=2,
            max_length=4,
            description="Two to four player ids to compare side by side.",
        ),
    ],
) -> list[PlayerDetail]:
    """Return full detail for several players at once.

    Bounded to four because the comparison view renders columns, and because an
    unbounded list would turn one request into an arbitrary amount of work.
    """
    session = registry.get(simulation_id)
    players = session.players_by_uuid
    missing = [uuid for uuid in player_uuid if uuid not in players]
    if missing:
        raise UnknownPlayerError(f"not in this draft pool: {', '.join(missing)}")
    return [player_detail(players[uuid], is_demo=session.is_demo) for uuid in player_uuid]


@router.post(
    "/{simulation_id}/advance",
    response_model=list[DraftPickOut],
    summary="Run computer picks",
)
async def advance(
    simulation_id: str, body: AdvanceRequest, registry: RegistryDep
) -> list[DraftPickOut]:
    """Advance the draft by computer picks, optionally up to the user's turn."""
    session = registry.get(simulation_id)
    picks = await registry.advance(
        session, picks=body.picks, stop_at_user_turn=body.stop_at_user_turn
    )
    players = session.players_by_uuid
    return [pick_out(p, players) for p in picks]


@router.post("/{simulation_id}/pick", response_model=DraftPickOut, summary="Make the user's pick")
async def make_pick(simulation_id: str, body: PickRequest, registry: RegistryDep) -> DraftPickOut:
    """Record the user's selection.

    Picking out of turn, or for a player already gone, is a 409 rather than a
    validation error: the request is well formed, the draft simply is not in a
    state that permits it.
    """
    session = registry.get(simulation_id)
    pick = await registry.make_user_pick(session, body.player_uuid)
    return pick_out(pick, session.players_by_uuid)


@router.post("/{simulation_id}/reset", response_model=SimulationState, summary="Reset the draft")
async def reset(simulation_id: str, registry: RegistryDep) -> SimulationState:
    """Return the simulation to its pristine seeded state."""
    session = registry.get(simulation_id)
    await registry.reset(session)
    return _state_of(session)


@router.get("/{simulation_id}/events", summary="Live draft event stream")
async def stream_events(
    simulation_id: str, request: Request, registry: RegistryDep
) -> EventSourceResponse:
    """Stream draft events over server-sent events.

    Each event carries a monotonic ``sequence``. A client that notices a gap -
    or receives ``resync_required`` - must re-read ``/board`` for canonical
    state rather than assuming it can reconstruct what it missed.

    A heartbeat comment is emitted periodically so intermediaries do not drop an
    idle connection during a paused draft.
    """
    session = registry.get(simulation_id)

    # Subscribe before the response starts streaming, so a pick published
    # between this handler running and the generator's first tick cannot be
    # missed. EventBus.subscribe registers eagerly for exactly this reason.
    subscription = await registry.event_bus.subscribe(session.session_id)

    async def publisher() -> AsyncIterator[dict[str, str]]:
        # Announce the current sequence immediately, so a reconnecting client
        # can tell at once whether it fell behind.
        yield DraftEvent(
            draft_id=session.session_id,
            type=EventType.CONNECTION_STATUS,
            sequence=registry.current_sequence(session.session_id),
            payload={"status": "LIVE", "is_demo": session.is_demo},
        ).to_sse()

        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(
                        subscription.__anext__(), timeout=HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    # Nothing happened for a while. The keep-alive is shaped
                    # like every other frame so a client never special-cases it,
                    # and it re-states the sequence so a client that missed
                    # events while idle notices the gap.
                    yield DraftEvent(
                        draft_id=session.session_id,
                        type=EventType.HEARTBEAT,
                        sequence=registry.current_sequence(session.session_id),
                    ).to_sse()
                    continue
                except StopAsyncIteration:
                    return
                yield event.to_sse()
        finally:
            await subscription.aclose()

    return EventSourceResponse(publisher())
