"""Rebuilding a live draft session after the API restarts.

The scenario these protect is draft night: the API process dies at pick 40 and
the war room must come back without the user reconnecting their league by hand.
"""

from __future__ import annotations

from typing import Any

import pytest

from fhe.api.services.draft_session import DraftSessionRegistry, SessionNotFoundError
from fhe.api.services.league_connect import connect_sleeper_draft
from fhe.api.services.session_recovery import find_recoverable, recover_session
from fhe.core.types import Position
from fhe.data.providers.sleeper import SleeperPick
from tests.integration.test_league_connect import (
    DRAFT_ID,
    LEAGUE_ID,
    USER_ID,
    FakeSleeper,
    make_draft,
    make_league,
    sleeper_pick,
)

pytestmark = pytest.mark.integration


async def _connect(
    session_factory: Any,
    registry: DraftSessionRegistry,
    picks: tuple[SleeperPick, ...] = (),
) -> tuple[Any, Any, Any]:
    """Connect a draft the way the API does, returning the live session."""
    sleeper = FakeSleeper(make_league(), make_draft(), picks)
    return await connect_sleeper_draft(
        session_factory,
        sleeper,
        registry,
        league_id=LEAGUE_ID,
        draft_id=DRAFT_ID,
        user_id=USER_ID,
    )


class TestRecovery:
    async def test_a_restart_rebuilds_the_session_from_the_provider(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """The core promise: the war room survives losing the API process."""
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))
        connected, _, before = await _connect(session_factory, registry, picks)
        assert before.draft_state.pick_count == 12

        # The restart: every in-memory session is gone, the database is not.
        restarted = DraftSessionRegistry(registry.event_bus)
        with pytest.raises(SessionNotFoundError):
            restarted.get(DRAFT_ID)

        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(), picks),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert recovered is not None
        assert recovered.session_id == connected.session_id
        assert recovered.draft_state.pick_count == 12
        assert recovered.user_draft_slot == 5

    async def test_recovery_does_not_duplicate_picks(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """Picks come from the provider, never from replaying stored state.

        The failure this rules out is seeding from the database *and* the
        provider, which would double every pick and corrupt every roster.
        """
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))
        await _connect(session_factory, registry, picks)

        restarted = DraftSessionRegistry(registry.event_bus)
        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(), picks),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert recovered is not None
        drafted = [pick.player_uuid for pick in recovered.draft_state.picks]
        assert len(drafted) == 12
        assert len(set(drafted)) == 12

    async def test_recovery_is_idempotent(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """Recovering twice converges rather than accumulating."""
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 7))
        await _connect(session_factory, registry, picks)

        restarted = DraftSessionRegistry(registry.event_bus)
        first = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(), picks),
            restarted,
            draft_id=DRAFT_ID,
        )
        second = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(), picks),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert first is not None and second is not None
        # The second call finds the session the first one registered rather
        # than building a competing one.
        assert first is second
        assert restarted.count == 1
        assert second.draft_state.pick_count == 6

    async def test_recovery_picks_up_picks_made_while_the_api_was_down(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """The provider is the authority, so the gap heals itself."""
        await _connect(
            session_factory, registry, tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 5))
        )

        # Four more picks happened while the process was dead.
        later = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 9))
        restarted = DraftSessionRegistry(registry.event_bus)
        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(), later),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert recovered is not None
        assert recovered.draft_state.pick_count == 8

    async def test_rosters_rebuild_correctly(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """A recovered roster matches the one the original session held."""
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))
        _, _, before = await _connect(session_factory, registry, picks)
        original = before.draft_state.roster(5).player_uuids

        restarted = DraftSessionRegistry(registry.event_bus)
        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(), picks),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert recovered is not None
        assert recovered.draft_state.roster(5).player_uuids == original
        assert len(original) == 1  # slot 5 picks once in the first twelve

    async def test_status_is_reconciled_against_the_provider(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """A draft that finished during the outage comes back finished."""
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))
        await _connect(session_factory, registry, picks)

        restarted = DraftSessionRegistry(registry.event_bus)
        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(status="complete"), picks),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert recovered is not None
        assert recovered.provider_status == "complete"
        assert recovered.is_complete

    async def test_the_board_is_computable_after_recovery(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """Recovery produces a session the engine can actually evaluate."""
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))
        await _connect(session_factory, registry, picks)

        restarted = DraftSessionRegistry(registry.event_bus)
        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(), picks),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert recovered is not None
        board = recovered.evaluate()
        assert board.recommendations
        # Drafted players are gone from the board, which is the check that
        # proves state was applied rather than merely counted.
        remaining = {rec.player_uuid for rec in board.recommendations}
        assert remaining.isdisjoint({f"p-{n:03d}" for n in range(12)})
        assert board.recommendations[0].position is Position.RB

    async def test_an_unknown_draft_is_not_recoverable(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """A simulation id or a typo must stay a 404, not become a session."""
        assert await find_recoverable(session_factory, "no-such-draft") is None

        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft()),
            registry,
            draft_id="no-such-draft",
        )
        assert recovered is None

    async def test_recovery_needs_no_user_id(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """The seat is read from the database, not re-derived from a user.

        A draft order keyed by a user id the recovering process never saw would
        otherwise silently lose the user's seat, and with it every roster-need
        term in the recommendation.
        """
        await _connect(session_factory, registry, ())

        restarted = DraftSessionRegistry(registry.event_bus)
        # The provider no longer reports a draft order at all.
        recovered = await recover_session(
            session_factory,
            FakeSleeper(make_league(), make_draft(draft_order={}), ()),
            restarted,
            draft_id=DRAFT_ID,
        )

        assert recovered is not None
        assert recovered.user_draft_slot == 5
