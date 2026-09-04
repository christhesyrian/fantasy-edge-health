"""Assembly of the full war-room payload from a session."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

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
from fhe.core.draft.engine import PlayerRecommendation
from fhe.core.simulation.pool import SYNTHETIC_SOURCE
from fhe.core.types import Position
from fhe.db.base import utcnow

# How many recent picks the ticker shows.
RECENT_PICK_COUNT = 12
# How deep the transmitted board goes. The full pool is hundreds of players and
# the table only ever renders the top slice; sending all of it would make every
# recompute a large payload for no benefit.
DEFAULT_BOARD_DEPTH = 120

# ...but depth alone truncates by rank, and whole positions rank below it. The
# late-round discount deliberately sinks kickers and defences, so a board cut at
# 120 contained none of either — and the client filters what it was sent, so
# selecting DST showed an empty table on a league that requires one. Carrying a
# few of every roster-eligible position costs a handful of rows and makes a
# position filter incapable of being empty while players exist.
MIN_PER_POSITION = 8


def _transmitted(
    recommendations: Sequence[PlayerRecommendation], depth: int
) -> list[PlayerRecommendation]:
    """The top `depth` by rank, plus a floor of each position, still in rank order.

    Rank order is preserved rather than grouping by position: the board is one
    ranked list and the client filters it, so re-ordering here would change what
    the unfiltered table shows.
    """
    head = list(recommendations[:depth])
    included = {r.player_uuid for r in head}
    per_position: Counter[Position] = Counter(r.position for r in head)

    extra: list[PlayerRecommendation] = []
    for rec in recommendations[depth:]:
        if per_position[rec.position] >= MIN_PER_POSITION:
            continue
        per_position[rec.position] += 1
        included.add(rec.player_uuid)
        extra.append(rec)

    if not extra:
        return head
    return [r for r in recommendations if r.player_uuid in included]


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

    # A board with no projections and no ADP still ranks, but on almost no
    # information. Saying so is the difference between an honest tool and a
    # confident-looking one.
    warnings = list(session.pool_warnings)
    if not sources:
        warnings.insert(
            0,
            "No projection or ADP source is loaded, so this ranking rests on "
            "roster need and scarcity alone.",
        )

    return DraftBoardOut(
        draft_id=session.session_id,
        is_demo=session.is_demo,
        status="complete" if session.is_complete else "drafting",
        current_pick=current_pick,
        current_round=current_round,
        next_user_pick=board.next_user_pick,
        picks_until_user_turn=board.picks_until_user_turn,
        is_user_on_the_clock=session.is_user_on_the_clock,
        league=league_out(league),
        recommendations=[recommendation_out(r) for r in _transmitted(board.recommendations, depth)],
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
        warnings=warnings,
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
