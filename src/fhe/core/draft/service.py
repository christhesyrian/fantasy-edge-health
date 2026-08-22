"""Composition root for draft intelligence.

One function, :func:`evaluate_draft`, turns "here is the draft state and the
player pool" into "here is the war room". Both the live Sleeper poller and the
mock simulator call exactly this, which is what guarantees a rehearsal in the
simulator is a real rehearsal.

It is still pure: no I/O, no clock, no randomness.
"""

from __future__ import annotations

from collections.abc import Sequence

from fhe.core.draft.board import DraftBoard, build_board
from fhe.core.draft.engine import DraftContext, rank_board
from fhe.core.draft.models import DraftablePlayer
from fhe.core.draft.roster import compute_roster_need
from fhe.core.draft.scarcity import build_tiers, compute_scarcity
from fhe.core.draft.state import DraftState
from fhe.core.draft.vorp import ReplacementBaseline, compute_replacement_baseline
from fhe.core.league import VALUED_POSITIONS


def evaluate_draft(
    state: DraftState,
    pool: Sequence[DraftablePlayer],
    *,
    user_draft_slot: int | None = None,
    baseline: ReplacementBaseline | None = None,
) -> DraftBoard:
    """Produce the complete war-room board for the current draft state.

    Args:
        state: Authoritative draft state.
        pool: The full player pool, drafted and undrafted alike.
        user_draft_slot: Whose perspective to evaluate from. Falls back to the
            league's configured slot.
        baseline: Pre-computed replacement baseline. Supplying this across
            successive picks avoids recomputing a value that, by design, does
            not change during a draft (see :mod:`fhe.core.draft.vorp`).

    Returns:
        The board, including recommendations, headline picks, scarcity and alerts.
    """
    league = state.league
    slot = user_draft_slot if user_draft_slot is not None else league.user_draft_slot

    drafted = state.drafted_player_uuids
    available = [p for p in pool if p.player_uuid not in drafted]

    replacement = baseline if baseline is not None else compute_replacement_baseline(pool, league)

    players_by_uuid = {p.player_uuid: p for p in pool}
    drafted_positions = (
        [
            players_by_uuid[uuid].position
            for uuid in state.roster(slot).player_uuids
            if uuid in players_by_uuid
        ]
        if slot is not None
        else []
    )
    roster_need = compute_roster_need(league, drafted_positions)

    picks_until_turn = state.picks_until_slot_turn(slot) if slot is not None else None
    user_picks_remaining = (
        sum(1 for p in league.picks_for_slot(slot) if p >= (state.current_pick_number or 1))
        if slot is not None
        else None
    )
    current_pick = state.current_pick_number or league.total_picks
    next_user_pick = state.next_pick_number_for_slot(slot) if slot is not None else None

    scarcity = compute_scarcity(
        available,
        picks_until_next_turn=picks_until_turn,
        replacement_points=replacement.points_by_position,
    )

    tier_by_player: dict[str, int] = {}
    for position in VALUED_POSITIONS:
        for tier in build_tiers(available, position):
            for uuid in tier.player_uuids:
                tier_by_player[uuid] = tier.tier

    roster_bye_weeks = tuple(
        players_by_uuid[uuid].bye_week
        for uuid in (state.roster(slot).player_uuids if slot is not None else ())
        if uuid in players_by_uuid and players_by_uuid[uuid].bye_week is not None
    )

    context = DraftContext(
        available=available,
        baseline=replacement,
        scarcity=scarcity,
        roster_need=roster_need,
        current_pick=current_pick,
        next_user_pick=next_user_pick,
        roster_bye_weeks=roster_bye_weeks,  # type: ignore[arg-type]
        tier_by_player=tier_by_player,
        user_picks_remaining=user_picks_remaining,
    )

    recommendations = rank_board(context)
    return build_board(
        recommendations,
        available,
        scarcity,
        picks_until_user_turn=picks_until_turn,
        current_pick=current_pick,
        next_user_pick=next_user_pick,
    )
