"""Live Sleeper draft poller.

Turns a provider that has no push mechanism into an event stream, without
hammering it and without ever corrupting the board.

Why polling at all
------------------
Sleeper publishes no webhook or socket for draft picks, so polling is the only
option. The question is how to do it without being blocked and without a
transient failure looking like a draft reset.

Design
------
* **Adaptive interval.** Fast while picks are landing, slower when the draft is
  idle, slower still while backing off from an error. A draft with a 90-second
  pick timer does not need a request every second.
* **Idempotency lives in the domain.** The poller hands every observed pick to
  :class:`~fhe.core.draft.state.DraftState`, which already resolves duplicates,
  out-of-order arrival, and batches. The poller adds no dedupe logic of its own,
  so live and simulated drafts converge through identical code.
* **Failure never wipes the board.** A provider error leaves the last known
  state intact and marks the draft stale. The one thing this must never do is
  interpret an error, or an empty response, as "no picks have been made".
* **Backoff is bounded and jittered**, so a provider having a bad minute is not
  turned into a retry storm.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, unique
from typing import Final, Protocol

from fhe.api.events import DraftEvent, EventBus, EventType, SequenceCounter
from fhe.config import Settings
from fhe.core.draft.models import DraftPick, PickOutcome
from fhe.core.draft.state import DraftState
from fhe.core.league import LeagueSettings
from fhe.data.providers.base import ProviderError
from fhe.data.providers.sleeper import SleeperDraft, SleeperPick
from fhe.db.base import utcnow
from fhe.observability import DRAFT_PICKS_INGESTED, DRAFT_POLLS, get_logger

log = get_logger(__name__)

# Interval multipliers applied to the configured base interval.
IDLE_INTERVAL_MULTIPLIER: Final = 3.0
# How long without a pick before the draft counts as idle.
IDLE_AFTER_SECONDS: Final = 90.0
# Consecutive failures before the poller reports the draft as stale to clients.
FAILURES_BEFORE_STALE: Final = 2
# Give up on a draft that has failed this many times in a row; something is
# wrong that retrying will not fix.
MAX_CONSECUTIVE_FAILURES: Final = 40


class DraftSource(Protocol):
    """The only two capabilities the poller needs from a provider.

    Depending on the narrow protocol rather than the concrete client keeps the
    poller honest about its requirements, and lets the resilience tests script a
    provider's behaviour without impersonating a full API client.
    """

    async def get_draft_picks(self, draft_id: str) -> tuple[SleeperPick, ...]:
        """Every pick the provider currently reports for a draft."""
        ...

    async def get_draft(self, draft_id: str) -> SleeperDraft | None:
        """Draft metadata, including its status."""
        ...


@unique
class PollerState(StrEnum):
    """What the poller is currently doing."""

    STARTING = "starting"
    LIVE = "live"
    BACKING_OFF = "backing_off"
    COMPLETE = "complete"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass
class PollerStatus:
    """Observable state, surfaced by diagnostics and the war room."""

    draft_id: str
    state: PollerState = PollerState.STARTING
    poll_count: int = 0
    picks_observed: int = 0
    duplicates_seen: int = 0
    conflicts_seen: int = 0
    consecutive_failures: int = 0
    last_success_at: datetime | None = None
    last_error: str | None = None
    current_interval_seconds: float = 0.0

    @property
    def is_stale(self) -> bool:
        """Whether clients should be told the feed is no longer trustworthy."""
        return self.consecutive_failures >= FAILURES_BEFORE_STALE

    def age_seconds(self, *, now: datetime | None = None) -> float | None:
        """Seconds since the last successful poll."""
        if self.last_success_at is None:
            return None
        return ((now or utcnow()) - self.last_success_at).total_seconds()


@dataclass(frozen=True, slots=True)
class DraftBinding:
    """Everything needed to poll one draft."""

    draft_id: str
    league: LeagueSettings
    user_draft_slot: int | None = None
    # Maps a Sleeper player id to an internal player uuid. Picks for players
    # outside this map are still recorded, with the provider id retained.
    player_id_map: dict[str, str] = field(default_factory=dict)


def to_domain_pick(pick: SleeperPick, binding: DraftBinding, *, observed_at: datetime) -> DraftPick:
    """Translate a provider pick into the domain type.

    A player we cannot resolve is *not* dropped. The provider id is retained so
    the pick still consumes its slot and still removes a player from the board;
    losing a pick because we failed to recognise a rookie would be far worse
    than showing an unresolved name.
    """
    player_uuid = binding.player_id_map.get(pick.player_id, f"sleeper:{pick.player_id}")
    return DraftPick(
        pick_no=pick.pick_no,
        round_number=pick.round_number,
        draft_slot=pick.draft_slot,
        player_uuid=player_uuid,
        roster_id=pick.roster_id,
        picked_by=pick.picked_by,
        is_keeper=pick.is_keeper,
        source_player_id=pick.player_id,
        observed_at=observed_at,
    )


class DraftPoller:
    """Polls one Sleeper draft and publishes what changes.

    Args:
        settings: Application settings, for intervals and provider limits.
        provider: Sleeper client.
        event_bus: Where draft events are published.
        binding: The draft to follow.
        state: Draft state to apply picks to. A fresh one is created if omitted.
        sequence: Shared sequence counter, so event numbering is consistent with
            any other publisher on the same channel.
    """

    def __init__(
        self,
        settings: Settings,
        provider: DraftSource,
        event_bus: EventBus,
        binding: DraftBinding,
        *,
        state: DraftState | None = None,
        sequence: SequenceCounter | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._bus = event_bus
        self._binding = binding
        self._state = state or DraftState(binding.league, draft_id=binding.draft_id)
        self._sequence = sequence or SequenceCounter()
        self._rng = rng or random.Random()  # noqa: S311 - backoff jitter, not crypto
        self._status = PollerStatus(draft_id=binding.draft_id)
        self._stop = asyncio.Event()

    @property
    def status(self) -> PollerStatus:
        """Current poller status."""
        return self._status

    @property
    def state(self) -> DraftState:
        """The draft state this poller maintains."""
        return self._state

    def request_stop(self) -> None:
        """Ask the poll loop to finish after the current cycle."""
        self._stop.set()

    # ------------------------------------------------------------------ loop

    async def run(self) -> PollerStatus:
        """Poll until the draft completes, fails persistently, or is stopped."""
        log.info("draft_poller_started", draft_id=self._binding.draft_id)

        while not self._stop.is_set():
            try:
                await self._poll_once()
            except ProviderError as error:
                self._record_failure(str(error))
                if self._status.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._status.state = PollerState.FAILED
                    log.error(
                        "draft_poller_gave_up",
                        draft_id=self._binding.draft_id,
                        failures=self._status.consecutive_failures,
                    )
                    break

            if self._status.state is PollerState.COMPLETE:
                break

            interval = self._next_interval()
            self._status.current_interval_seconds = round(interval, 2)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                continue

        if self._status.state not in {PollerState.COMPLETE, PollerState.FAILED}:
            self._status.state = PollerState.STOPPED
        log.info(
            "draft_poller_stopped",
            draft_id=self._binding.draft_id,
            state=self._status.state.value,
            polls=self._status.poll_count,
            picks=self._status.picks_observed,
        )
        return self._status

    async def _poll_once(self) -> None:
        """One poll cycle: read, reconcile, publish."""
        self._status.poll_count += 1
        observed_at = utcnow()

        picks = await self._provider.get_draft_picks(self._binding.draft_id)
        draft = await self._provider.get_draft(self._binding.draft_id)

        DRAFT_POLLS.labels("success").inc()
        recovered = self._status.consecutive_failures > 0
        self._status.consecutive_failures = 0
        self._status.last_error = None
        self._status.last_success_at = observed_at
        self._status.state = PollerState.LIVE

        if recovered:
            await self._publish(EventType.CONNECTION_STATUS, {"status": "LIVE", "recovered": True})

        applied = self._apply(picks, observed_at=observed_at)

        for pick in applied:
            await self._publish(
                EventType.PICK_MADE,
                {
                    "pick_no": pick.pick_no,
                    "round": pick.round_number,
                    "draft_slot": pick.draft_slot,
                    "player_uuid": pick.player_uuid,
                    "source_player_id": pick.source_player_id,
                },
            )
        if applied:
            await self._publish(
                EventType.BOARD_UPDATED,
                {
                    "current_pick": self._state.current_pick_number,
                    "picks_made": self._state.pick_count,
                },
            )

        if self._is_complete(draft):
            self._status.state = PollerState.COMPLETE
            await self._publish(EventType.DRAFT_COMPLETE, {})

    def _apply(self, picks: tuple[SleeperPick, ...], *, observed_at: datetime) -> list[DraftPick]:
        """Reconcile provider picks against draft state.

        The provider re-sends every pick on every poll, so most of this batch is
        expected to be a duplicate. That is the normal case, not an error.
        """
        domain = [to_domain_pick(pick, self._binding, observed_at=observed_at) for pick in picks]
        results = self._state.apply_picks(domain)

        applied: list[DraftPick] = []
        for result in results:
            if result.outcome is PickOutcome.APPLIED:
                applied.append(result.pick)
                DRAFT_PICKS_INGESTED.labels("applied").inc()
            elif result.outcome is PickOutcome.DUPLICATE:
                self._status.duplicates_seen += 1
                DRAFT_PICKS_INGESTED.labels("duplicate").inc()
            else:
                # A conflict means the provider disagrees with history we already
                # recorded. Never silently overwrite it; surface it instead.
                self._status.conflicts_seen += 1
                DRAFT_PICKS_INGESTED.labels("conflict").inc()
                log.warning(
                    "draft_pick_conflict",
                    draft_id=self._binding.draft_id,
                    pick_no=result.pick.pick_no,
                    incoming_player=result.pick.player_uuid,
                    existing_player=(result.existing.player_uuid if result.existing else None),
                )

        self._status.picks_observed += len(applied)
        return applied

    def _is_complete(self, draft: SleeperDraft | None) -> bool:
        """Whether the draft is finished, by the provider's word or by arithmetic."""
        if draft is not None and draft.status == "complete":
            return True
        return self._state.is_complete

    def _record_failure(self, message: str) -> None:
        """Note a failed poll without disturbing draft state."""
        DRAFT_POLLS.labels("failure").inc()
        self._status.consecutive_failures += 1
        self._status.last_error = message
        self._status.state = PollerState.BACKING_OFF
        log.warning(
            "draft_poll_failed",
            draft_id=self._binding.draft_id,
            consecutive_failures=self._status.consecutive_failures,
            error=message,
            note="last known draft state retained",
        )

    def _next_interval(self) -> float:
        """Choose the delay before the next poll.

        Three regimes: backing off after failures, slow while the draft is idle,
        and the configured base interval while picks are landing.
        """
        base = self._settings.draft_poll_interval_seconds
        ceiling = self._settings.draft_poll_max_interval_seconds

        if self._status.consecutive_failures:
            backoff = min(ceiling, base * 2 ** min(self._status.consecutive_failures, 6))
            # Full jitter, so many clients recovering together do not synchronise.
            return self._rng.uniform(base, backoff)

        age = self._status.age_seconds()
        last_pick = self._state.picks[-1].observed_at if self._state.picks else None
        idle = last_pick is None or (utcnow() - last_pick) > timedelta(seconds=IDLE_AFTER_SECONDS)
        if idle and age is not None:
            return min(ceiling, base * IDLE_INTERVAL_MULTIPLIER)
        return base

    async def _publish(self, event_type: EventType, payload: dict[str, object]) -> None:
        """Publish an event on this draft's channel."""
        sequence = await self._sequence.next(self._binding.draft_id)
        await self._bus.publish(
            DraftEvent(
                draft_id=self._binding.draft_id,
                type=event_type,
                sequence=sequence,
                payload=dict(payload),
            )
        )
