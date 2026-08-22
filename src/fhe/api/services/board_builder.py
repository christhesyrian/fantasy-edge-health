"""Assembly of the full war-room payload from a session."""

from __future__ import annotations

from fhe.api.mappers import (
    alert_out,
    league_out,
    pick_out,
    recommendation_out,
    roster_out,
    scarcity_out,
)
from fhe.api.schemas import DraftBoardOut, Provenance
from fhe.api.services.draft_session import DraftSession
from fhe.core.simulation.pool import SYNTHETIC_SOURCE
from fhe.db.base import utcnow

# How many recent picks the ticker shows.
RECENT_PICK_COUNT = 12
# How deep the transmitted board goes. The full pool is hundreds of players and
# the table only ever renders the top slice; sending all of it would make every
# recompute a large payload for no benefit.
DEFAULT_BOARD_DEPTH = 120


def build_board_payload(
    session: DraftSession, *, depth: int = DEFAULT_BOARD_DEPTH
) -> DraftBoardOut:
    """Render a session's current board as the API contract.

    Args:
        session: The session to render.
        depth: How many ranked players to include.
    """
    board = session.last_board or session.evaluate()
    state = session.draft_state
    players = session.players_by_uuid
    league = session.league

    current_pick = board.current_pick
    current_round = (current_pick - 1) // league.team_count + 1 if current_pick else None

    my_roster = None
    slot = session.user_draft_slot
    if slot is not None:
        my_roster = roster_out(
            league,
            slot,
            state.roster(slot).player_uuids,
            players,
            roster_id=state.roster(slot).roster_id,
            is_user=True,
        )

    sources = sorted(
        {
            source
            for player in session.pool
            for source in (player.projection_source, player.adp_source)
            if source
        }
    )

    return DraftBoardOut(
        draft_id=session.session_id,
        is_demo=session.is_demo,
        status="complete" if state.is_complete else "drafting",
        current_pick=current_pick,
        current_round=current_round,
        next_user_pick=board.next_user_pick,
        picks_until_user_turn=board.picks_until_user_turn,
        is_user_on_the_clock=session.is_user_on_the_clock,
        league=league_out(league),
        recommendations=[recommendation_out(r) for r in board.recommendations[:depth]],
        best_pick=recommendation_out(board.best_pick) if board.best_pick else None,
        safest_pick=recommendation_out(board.safest_pick) if board.safest_pick else None,
        highest_upside=(recommendation_out(board.highest_upside) if board.highest_upside else None),
        best_value=recommendation_out(board.best_value) if board.best_value else None,
        scarcity=[scarcity_out(s) for s in board.scarcity.values()],
        alerts=[alert_out(a) for a in board.alerts],
        my_roster=my_roster,
        recent_picks=[pick_out(p, players) for p in reversed(state.picks[-RECENT_PICK_COUNT:])],
        computed_at=utcnow(),
        computation_ms=session.last_computation_ms,
        provenance=[
            Provenance(
                source=source,
                observed_at=session.created_at,
                # Synthetic demo data cannot go stale; it is regenerated with
                # the session and labelled so it is never read as real.
                freshness="LIVE" if source == SYNTHETIC_SOURCE else "FRESH",
            )
            for source in sources
        ],
    )
