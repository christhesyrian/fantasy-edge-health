"""Draft endpoints, shared by live and simulated drafts.

A draft is a draft. Whether picks arrive from a Sleeper poller or from the mock
simulator changes nothing about how a board is read, so these endpoints serve
both and the client does not branch on which it is looking at.

Simulation-only *actions* — advancing, picking on the user's behalf, resetting —
live in :mod:`fhe.api.routers.simulations`, because those genuinely do not exist
for a real draft.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sse_starlette.sse import EventSourceResponse

from fhe.api.deps import PollerManagerDep, RegistryDep, SessionFactoryDep, SettingsDep
from fhe.api.events import DraftEvent, EventType
from fhe.api.mappers import player_detail
from fhe.api.schemas import (
    DraftBoardOut,
    DraftStateOut,
    PlayerDetail,
    PollerStatusOut,
)
from fhe.api.services.board_builder import DEFAULT_BOARD_DEPTH, build_board_payload
from fhe.api.services.draft_session import DraftSession, SessionNotFoundError
from fhe.api.services.session_recovery import recover_session
from fhe.core.errors import UnknownPlayerError
from fhe.data.providers.sleeper import SleeperProvider
from fhe.observability import get_logger

router = APIRouter(prefix="/drafts", tags=["drafts"])
log = get_logger(__name__)

# Keep-alive cadence. Proxies commonly drop an idle connection at 60 seconds,
# and a draft between picks is legitimately quiet for longer.
HEARTBEAT_SECONDS = 15.0


def _state_of(session: DraftSession, poller: PollerStatusOut | None) -> DraftStateOut:
    """Summarise a draft's lifecycle."""
    state = session.draft_state
    return DraftStateOut(
        draft_id=session.session_id,
        is_demo=session.is_demo,
        status="complete" if session.is_complete else "drafting",
        pick_count=state.pick_count,
        total_picks=session.league.total_picks,
        current_pick=state.current_pick_number,
        is_complete=session.is_complete,
        is_user_on_the_clock=session.is_user_on_the_clock,
        user_draft_slot=session.user_draft_slot,
        created_at=session.created_at,
        poller=poller,
    )


def _poller_status(manager: PollerManagerDep, draft_id: str) -> PollerStatusOut | None:
    """Poller status for a live draft, or ``None`` for a simulation."""
    status_record = manager.status(draft_id)
    if status_record is None:
        return None
    return PollerStatusOut(
        state=status_record.state.value,
        poll_count=status_record.poll_count,
        picks_observed=status_record.picks_observed,
        consecutive_failures=status_record.consecutive_failures,
        is_stale=status_record.is_stale,
        last_success_at=status_record.last_success_at,
        last_error=status_record.last_error,
        seconds_since_success=status_record.age_seconds(),
    )


async def resolve_session(
    draft_id: str,
    registry: RegistryDep,
    settings: SettingsDep,
    session_factory: SessionFactoryDep,
    pollers: PollerManagerDep,
) -> DraftSession:
    """The session for a draft, rebuilding it from persisted state if needed.

    Sessions are in-memory, so an API restart mid-draft would otherwise leave a
    connected league unreadable until the user reconnected it by hand. A live
    draft is reconstructable — the provider owns the picks and the database owns
    the league — so a miss is repaired rather than reported.

    A simulation that has been evicted or never existed is still a 404: it has
    no persisted metadata to rebuild from, and inventing one would be worse than
    saying so.
    """
    try:
        return registry.get(draft_id)
    except SessionNotFoundError:
        pass

    async with SleeperProvider(settings) as sleeper:
        recovered = await recover_session(
            session_factory,
            sleeper,
            registry,
            draft_id=draft_id,
            pollers=pollers,
            # The poller outlives this request, so it gets its own client
            # rather than the one closing with this block.
            poller_provider_factory=lambda: SleeperProvider(settings),
        )
    if recovered is None:
        raise SessionNotFoundError(f"no draft session {draft_id!r}")
    return recovered


SessionDep = Annotated[DraftSession, Depends(resolve_session)]


@router.get("/{draft_id}", response_model=DraftStateOut, summary="Draft state")
async def get_state(draft_id: str, session: SessionDep, pollers: PollerManagerDep) -> DraftStateOut:
    """Lifecycle summary, including live poller health when there is one."""
    return _state_of(session, _poller_status(pollers, draft_id))


@router.get("/{draft_id}/board", response_model=DraftBoardOut, summary="Draft board")
async def get_board(
    session: SessionDep,
    depth: int = Query(
        default=DEFAULT_BOARD_DEPTH,
        ge=1,
        le=500,
        description="How many ranked players to return.",
    ),
) -> DraftBoardOut:
    """The complete war-room view.

    Also the canonical-state read a client performs after a dropped event
    stream, rather than trying to replay the gap.
    """
    session.evaluate()
    return build_board_payload(session, depth=depth)


@router.get(
    "/{draft_id}/players/{player_uuid}",
    response_model=PlayerDetail,
    summary="Player detail",
)
async def get_player(player_uuid: str, session: SessionDep) -> PlayerDetail:
    """Everything the player drawer needs, without leaving draft context."""
    player = session.players_by_uuid.get(player_uuid)
    if player is None:
        raise UnknownPlayerError(f"no player {player_uuid!r} in this draft pool")
    return player_detail(player, is_demo=session.is_demo)


@router.get("/{draft_id}/compare", response_model=list[PlayerDetail], summary="Compare players")
async def compare_players(
    session: SessionDep,
    player_uuid: Annotated[
        list[str],
        Query(
            min_length=2,
            max_length=4,
            description="Two to four player ids to compare side by side.",
        ),
    ],
) -> list[PlayerDetail]:
    """Full detail for several players at once.

    Bounded at four because the comparison view renders columns, and an
    unbounded list would turn one request into arbitrary work.
    """
    players = session.players_by_uuid
    missing = [uuid for uuid in player_uuid if uuid not in players]
    if missing:
        raise UnknownPlayerError(f"not in this draft pool: {', '.join(missing)}")
    return [player_detail(players[uuid], is_demo=session.is_demo) for uuid in player_uuid]


@router.post(
    "/{draft_id}/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop following a live draft",
)
async def disconnect(draft_id: str, pollers: PollerManagerDep) -> None:
    """Stop polling a draft.

    The session and its board survive; only provider polling stops. Draft state
    already lives in the session, so nothing is lost by disconnecting.
    """
    stopped = await pollers.stop(draft_id)
    log.info("draft_disconnect_requested", draft_id=draft_id, was_running=stopped)


@router.get(
    "/{draft_id}/events",
    summary="Live draft event stream",
    status_code=status.HTTP_200_OK,
)
async def stream_events(
    request: Request, session: SessionDep, registry: RegistryDep
) -> EventSourceResponse:
    """Stream draft events over server-sent events.

    Each event carries a monotonic ``sequence``. A client that notices a gap, or
    receives ``resync_required``, re-reads ``/board`` for canonical state rather
    than assuming it can reconstruct what it missed.

    Events are *named*, so a browser client must register a listener per type —
    ``EventSource.onmessage`` fires only for unnamed events and would silently
    receive nothing.
    """
    # Subscribe before the response starts streaming, so a pick published
    # between this handler running and the generator's first tick cannot be
    # missed. EventBus.subscribe registers eagerly for exactly this reason.
    subscription = await registry.event_bus.subscribe(session.session_id)

    async def publisher() -> AsyncIterator[dict[str, str]]:
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
                    # Shaped like every other frame so a client never has to
                    # special-case it, and it restates the sequence so an idle
                    # client notices anything it missed.
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
