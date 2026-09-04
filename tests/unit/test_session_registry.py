"""Reconnecting to a live draft.

Both cases here were found during a real Sleeper draft, and both were invisible
in the simulator: a mock draft is created once and never reconnected, so the
refresh path never ran.
"""

from __future__ import annotations

import pytest

from fhe.api.events import InProcessEventBus
from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.core.draft.models import DraftablePlayer, DraftPick
from fhe.core.draft.state import DraftState
from fhe.core.draft.vorp import ReplacementBaseline
from fhe.core.league import LeagueSettings
from fhe.core.types import DraftType, Position, ScoringFormat
from fhe.db.base import utcnow
from tests.conftest import make_player

pytestmark = pytest.mark.unit

DRAFT_ID = "draft-1"


def league(*, user_draft_slot: int | None) -> LeagueSettings:
    """A 12-team PPR league, optionally with the user's seat known."""
    return LeagueSettings.from_tokens(
        team_count=12,
        roster_position_tokens=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"],
        scoring_format=ScoringFormat.PPR,
        draft_type=DraftType.SNAKE,
        user_draft_slot=user_draft_slot,
    )


def pool() -> tuple[DraftablePlayer, ...]:
    return tuple(
        make_player(f"p-{i:03d}", Position.RB, projected_points=200.0 - i, adp=float(i + 1))
        for i in range(20)
    )


def pick(number: int) -> DraftPick:
    """One provider pick."""
    return DraftPick(
        pick_no=number,
        round_number=(number - 1) // 12 + 1,
        draft_slot=((number - 1) % 12) + 1,
        player_uuid=f"p-{number - 1:03d}",
        observed_at=utcnow(),
    )


def state_with(picks: int, *, settings: LeagueSettings) -> DraftState:
    """A draft state seeded with the first `picks` picks."""
    state = DraftState(settings, draft_id=DRAFT_ID)
    state.apply_picks([pick(n) for n in range(1, picks + 1)])
    return state


def register(registry: DraftSessionRegistry, settings: LeagueSettings, state: DraftState):  # type: ignore[no-untyped-def]
    """Register or refresh, the way connecting does."""
    return registry.register_live(
        session_id=DRAFT_ID,
        league=settings,
        pool=pool(),
        state=state,
        baseline=ReplacementBaseline(
            points_by_position={}, replacement_rank={}, players_considered=0
        ),
    )


class TestReconnect:
    def test_reconnecting_picks_up_a_draft_slot_assigned_later(self) -> None:
        """Sleeper assigns the draft order *after* people connect.

        Connecting pre-draft therefore records no seat, and reconnecting once
        the order exists is exactly how a user fixes that. Keeping the original
        league made that reconnect a no-op, so the board could never say whose
        turn it was.
        """
        registry = DraftSessionRegistry(InProcessEventBus())
        before = league(user_draft_slot=None)
        register(registry, before, state_with(0, settings=before))

        after = league(user_draft_slot=8)
        session = register(registry, after, state_with(0, settings=after))

        assert session.user_draft_slot == 8

    def test_reconnecting_does_not_orphan_a_running_poller(self) -> None:
        """The poller is handed `session.draft_state` and keeps that reference.

        `PollerManager.start` is idempotent, so a reconnect does not rebuild it.
        Rebinding the session to a *new* state object therefore left the poller
        writing somewhere nothing read, and the board frozen at whatever pick
        the reconnect happened to observe.
        """
        registry = DraftSessionRegistry(InProcessEventBus())
        settings = league(user_draft_slot=8)
        session = register(registry, settings, state_with(4, settings=settings))
        # What the poller captured when it started.
        poller_state = session.draft_state

        register(registry, settings, state_with(6, settings=settings))

        assert session.draft_state is poller_state, "state object must not be rebound"
        assert poller_state.pick_count == 6, "new picks must be merged into it"

        # And the poller writing to its reference is still what the board reads.
        poller_state.apply_picks([pick(7)])
        assert registry.get(DRAFT_ID).draft_state.pick_count == 7

    def test_state_only_moves_forward(self) -> None:
        """A reconnect that observes fewer picks must not roll the draft back."""
        registry = DraftSessionRegistry(InProcessEventBus())
        settings = league(user_draft_slot=8)
        session = register(registry, settings, state_with(9, settings=settings))

        register(registry, settings, state_with(3, settings=settings))

        assert session.draft_state.pick_count == 9

    def test_reconnecting_keeps_one_session(self) -> None:
        """Two sessions would mean two pollers against one draft."""
        registry = DraftSessionRegistry(InProcessEventBus())
        settings = league(user_draft_slot=8)
        first = register(registry, settings, state_with(0, settings=settings))
        second = register(registry, settings, state_with(2, settings=settings))

        assert first is second
        assert registry.count == 1
