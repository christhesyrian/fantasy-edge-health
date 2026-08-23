"""Mock draft endpoints.

This is the demo experience: a reviewer with no Sleeper account, no credentials,
and no ingested data can start a draft here and watch the war room work.

Only the actions *specific to a simulation* live here — creating one, advancing
the computer teams, picking on the user's behalf, resetting. Reading the board,
streaming events, and inspecting a player are the same operations for a real
draft, so they live under ``/drafts/{id}`` and serve both.

The simulation drives the same engine the live draft uses, so it is a genuine
rehearsal rather than a parallel implementation.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from fhe.api.deps import DemoPoolDep, RegistryDep
from fhe.api.mappers import pick_out
from fhe.api.schemas import (
    AdvanceRequest,
    DraftPickOut,
    PickRequest,
    SimulationCreate,
    SimulationState,
)
from fhe.api.services.draft_session import DraftSession
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
