"""Live draft poller: the failure modes that actually happen on draft night.

This file is the directive's live-draft quality bar (§40) expressed as tests.
Every case here is something a real provider does, and every one of them must
leave the board correct.
"""

from __future__ import annotations

from typing import Any

import pytest

from fhe.api.events import EventType, InProcessEventBus
from fhe.config import Settings
from fhe.core.league import LeagueSettings
from fhe.data.providers.base import (
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from fhe.data.providers.sleeper import SleeperDraft, SleeperPick
from fhe.worker.draft_poller import (
    DraftBinding,
    DraftPoller,
    PollerState,
    to_domain_pick,
)

pytestmark = pytest.mark.unit

DRAFT_ID = "draft-1"


def sleeper_pick(pick_no: int, player_id: str, *, slot: int | None = None) -> SleeperPick:
    """A provider pick for a 12-team draft."""
    return SleeperPick(
        draft_id=DRAFT_ID,
        pick_no=pick_no,
        round_number=(pick_no - 1) // 12 + 1,
        draft_slot=slot if slot is not None else ((pick_no - 1) % 12) + 1,
        player_id=player_id,
        roster_id=((pick_no - 1) % 12) + 1,
        picked_by=f"user-{pick_no}",
        is_keeper=False,
        metadata={},
    )


def sleeper_draft(status: str = "drafting") -> SleeperDraft:
    """Draft metadata as the provider returns it."""
    return SleeperDraft(
        draft_id=DRAFT_ID,
        league_id="league-1",
        status=status,
        draft_type="snake",
        season="2026",
        settings={"teams": 12, "rounds": 15},
        metadata={"scoring_type": "ppr"},
        draft_order={},
        slot_to_roster_id={},
    )


class ScriptedProvider:
    """A Sleeper stand-in whose every response is scripted.

    Each entry is either a list of picks or an exception to raise, so a test
    reads as the sequence of things the provider did.
    """

    def __init__(self, script: list[Any], *, draft_status: str = "drafting") -> None:
        self._script = list(script)
        self._draft_status = draft_status
        self.calls = 0

    async def get_draft_picks(self, draft_id: str) -> tuple[SleeperPick, ...]:
        """Return the next scripted response."""
        self.calls += 1
        entry = self._script.pop(0) if self._script else []
        if isinstance(entry, Exception):
            raise entry
        return tuple(entry)

    async def get_draft(self, draft_id: str) -> SleeperDraft:
        """Return draft metadata."""
        return sleeper_draft(self._draft_status)


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        draft_poll_interval_seconds=0.01,
        draft_poll_max_interval_seconds=0.02,
    )


@pytest.fixture
def binding(league: LeagueSettings) -> DraftBinding:
    return DraftBinding(
        draft_id=DRAFT_ID,
        league=league,
        user_draft_slot=5,
        player_id_map={f"sp{i}": f"uuid-{i}" for i in range(1, 40)},
    )


async def run_poller(
    settings: Settings,
    binding: DraftBinding,
    provider: ScriptedProvider,
    *,
    cycles: int,
) -> tuple[DraftPoller, list[Any]]:
    """Run a fixed number of poll cycles and collect published events."""
    bus = InProcessEventBus()
    poller = DraftPoller(settings, provider, bus, binding)

    subscription = await bus.subscribe(DRAFT_ID)
    events: list[Any] = []

    for _ in range(cycles):
        try:
            await poller._poll_once()
        except Exception as error:  # noqa: BLE001 - the poller's run loop does this too
            poller._record_failure(str(error))

    # Drain without blocking on an empty queue.
    while True:
        try:
            events.append(await __import__("asyncio").wait_for(subscription.__anext__(), 0.05))
        except (TimeoutError, StopAsyncIteration):
            break
    await subscription.aclose()
    return poller, events


class TestPickIngestion:
    async def test_new_picks_are_applied_and_published(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        provider = ScriptedProvider([[sleeper_pick(1, "sp1"), sleeper_pick(2, "sp2")]])
        poller, events = await run_poller(settings, binding, provider, cycles=1)

        assert poller.state.pick_count == 2
        picks = [e for e in events if e.type is EventType.PICK_MADE]
        assert len(picks) == 2
        assert any(e.type is EventType.BOARD_UPDATED for e in events)

    async def test_the_provider_resending_everything_is_the_normal_case(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        """Sleeper returns the full pick list every poll; only new ones count."""
        batch = [sleeper_pick(1, "sp1"), sleeper_pick(2, "sp2")]
        provider = ScriptedProvider([batch, batch, batch])
        poller, events = await run_poller(settings, binding, provider, cycles=3)

        assert poller.state.pick_count == 2
        assert len([e for e in events if e.type is EventType.PICK_MADE]) == 2
        assert poller.status.duplicates_seen == 4

    async def test_several_picks_arriving_at_once_are_applied_in_order(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        provider = ScriptedProvider(
            [[sleeper_pick(1, "sp1")], [sleeper_pick(n, f"sp{n}") for n in range(1, 6)]]
        )
        poller, _ = await run_poller(settings, binding, provider, cycles=2)

        assert [p.pick_no for p in poller.state.picks] == [1, 2, 3, 4, 5]

    async def test_out_of_order_responses_still_produce_ordered_state(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        shuffled = [sleeper_pick(3, "sp3"), sleeper_pick(1, "sp1"), sleeper_pick(2, "sp2")]
        provider = ScriptedProvider([shuffled])
        poller, _ = await run_poller(settings, binding, provider, cycles=1)

        assert [p.pick_no for p in poller.state.picks] == [1, 2, 3]

    async def test_a_pick_is_never_duplicated_in_state(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        provider = ScriptedProvider([[sleeper_pick(1, "sp1")]] * 5)
        poller, _ = await run_poller(settings, binding, provider, cycles=5)

        assert poller.state.pick_count == 1

    async def test_a_conflicting_pick_does_not_overwrite_history(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        """If the provider changes who was taken at a pick, keep what we recorded."""
        provider = ScriptedProvider([[sleeper_pick(1, "sp1")], [sleeper_pick(1, "sp2")]])
        poller, _ = await run_poller(settings, binding, provider, cycles=2)

        assert poller.state.picks[0].player_uuid == "uuid-1"
        assert poller.status.conflicts_seen == 1

    async def test_an_unrecognised_player_still_consumes_the_pick(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        """Losing a pick because we failed to recognise a rookie is worse than
        showing an unresolved name."""
        provider = ScriptedProvider([[sleeper_pick(1, "unknown-rookie")]])
        poller, _ = await run_poller(settings, binding, provider, cycles=1)

        assert poller.state.pick_count == 1
        assert poller.state.picks[0].player_uuid == "sleeper:unknown-rookie"
        assert poller.state.picks[0].source_player_id == "unknown-rookie"


class TestFailureHandling:
    async def test_a_provider_failure_never_wipes_the_board(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        """The single most important property in this file."""
        provider = ScriptedProvider(
            [
                [sleeper_pick(1, "sp1"), sleeper_pick(2, "sp2")],
                ProviderUnavailableError("503", provider="sleeper", operation="get_draft_picks"),
                ProviderTimeoutError("timeout", provider="sleeper", operation="get_draft_picks"),
            ]
        )
        poller, _ = await run_poller(settings, binding, provider, cycles=3)

        assert poller.state.pick_count == 2, "picks were lost during an outage"
        assert poller.status.consecutive_failures == 2
        assert poller.status.state is PollerState.BACKING_OFF

    async def test_recovery_restores_live_state_and_announces_it(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        provider = ScriptedProvider(
            [
                ProviderTimeoutError("t", provider="sleeper", operation="get_draft_picks"),
                [sleeper_pick(1, "sp1")],
            ]
        )
        poller, events = await run_poller(settings, binding, provider, cycles=2)

        assert poller.status.state is PollerState.LIVE
        assert poller.status.consecutive_failures == 0
        assert any(
            e.type is EventType.CONNECTION_STATUS and e.payload.get("recovered") for e in events
        )

    async def test_repeated_failures_mark_the_feed_stale(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        provider = ScriptedProvider(
            [ProviderTimeoutError("t", provider="s", operation="o") for _ in range(3)]
        )
        poller, _ = await run_poller(settings, binding, provider, cycles=3)

        assert poller.status.is_stale
        assert poller.status.last_error

    async def test_an_empty_response_is_not_treated_as_a_reset(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        """An empty pick list must never un-draft anybody."""
        provider = ScriptedProvider([[sleeper_pick(1, "sp1")], []])
        poller, _ = await run_poller(settings, binding, provider, cycles=2)

        assert poller.state.pick_count == 1


class TestIntervals:
    def test_backoff_grows_with_consecutive_failures(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        import random

        bus = InProcessEventBus()
        poller = DraftPoller(
            settings,
            ScriptedProvider([]),
            bus,
            binding,
            rng=random.Random(1),
        )

        poller.status.consecutive_failures = 1
        first = poller._next_interval()
        poller.status.consecutive_failures = 5
        later = poller._next_interval()

        assert later >= first
        assert later <= settings.draft_poll_max_interval_seconds

    def test_interval_never_exceeds_the_configured_ceiling(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        bus = InProcessEventBus()
        poller = DraftPoller(settings, ScriptedProvider([]), bus, binding)
        poller.status.consecutive_failures = 50

        assert poller._next_interval() <= settings.draft_poll_max_interval_seconds


class TestCompletion:
    async def test_a_complete_draft_is_announced_and_ends_the_loop(
        self, settings: Settings, binding: DraftBinding
    ) -> None:
        provider = ScriptedProvider([[sleeper_pick(1, "sp1")]], draft_status="complete")
        poller, events = await run_poller(settings, binding, provider, cycles=1)

        assert poller.status.state is PollerState.COMPLETE
        assert any(e.type is EventType.DRAFT_COMPLETE for e in events)


class TestTranslation:
    def test_resolved_players_use_the_internal_uuid(self, binding: DraftBinding) -> None:
        from fhe.db.base import utcnow

        pick = to_domain_pick(sleeper_pick(1, "sp3"), binding, observed_at=utcnow())
        assert pick.player_uuid == "uuid-3"
        assert pick.source_player_id == "sp3"

    def test_the_seat_and_the_roster_are_kept_separate(self, binding: DraftBinding) -> None:
        """They differ when a pick has been traded, so neither is derived."""
        from fhe.db.base import utcnow

        provider_pick = sleeper_pick(1, "sp1", slot=4)
        pick = to_domain_pick(provider_pick, binding, observed_at=utcnow())

        assert pick.draft_slot == 4
        assert pick.roster_id == provider_pick.roster_id
