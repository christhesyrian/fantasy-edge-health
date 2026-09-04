"""Supervises one live poller per connected draft.

A poller is a long-lived task, and the API process is the thing that owns its
lifetime. This keeps that ownership in one place so a draft can never end up
with two pollers hammering the provider, and so every task is cancelled on
shutdown rather than leaking.

After each poll cycle that changed anything, the manager asks the session to
re-evaluate and publish, which is how a live pick reaches the browser.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fhe.api.services.draft_session import DraftSession, DraftSessionRegistry
from fhe.config import Settings
from fhe.core.errors import DomainError
from fhe.observability import get_logger
from fhe.worker.draft_poller import (
    DraftBinding,
    DraftPoller,
    DraftSource,
    PickRecorder,
    PollerStatus,
)

log = get_logger(__name__)

# A hard ceiling on concurrent live drafts in one process. Sleeper's rate limit
# is per IP and therefore shared across every draft this process follows, so an
# unbounded count is how a busy afternoon turns into an IP block.
MAX_CONCURRENT_POLLERS = 12


class TooManyDraftsError(DomainError):
    """This process is already following as many drafts as it safely can."""


@dataclass(slots=True)
class _Supervised:
    """A running poller and the task driving it."""

    poller: DraftPoller
    task: asyncio.Task[PollerStatus]
    session: DraftSession


class PollerManager:
    """Starts, tracks, and stops live draft pollers."""

    def __init__(
        self,
        settings: Settings,
        registry: DraftSessionRegistry,
        recorder: PickRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._registry = registry
        # Supplied here rather than at each start site so a live draft cannot
        # be polled without its picks being written down, whether it was
        # connected fresh or rebuilt after a restart.
        self._recorder = recorder
        self._running: dict[str, _Supervised] = {}

    @property
    def recorder(self) -> PickRecorder | None:
        """Where this manager's pollers write their picks.

        Exposed so a connect can record the picks already made before its poller
        starts, without every call site needing its own wiring.
        """
        return self._recorder

    @property
    def active_draft_ids(self) -> tuple[str, ...]:
        """Drafts currently being polled."""
        return tuple(self._running)

    @property
    def count(self) -> int:
        """How many pollers are running."""
        return len(self._running)

    def status(self, draft_id: str) -> PollerStatus | None:
        """Current status of one poller, if it is running."""
        supervised = self._running.get(draft_id)
        return supervised.poller.status if supervised else None

    def start(
        self,
        provider: DraftSource,
        binding: DraftBinding,
        session: DraftSession,
    ) -> PollerStatus:
        """Begin polling a draft, or return the existing poller's status.

        Idempotent by draft id: connecting twice to the same draft must not
        double the request rate against the provider.
        """
        existing = self._running.get(binding.draft_id)
        if existing is not None and not existing.task.done():
            log.info("poller_already_running", draft_id=binding.draft_id)
            return existing.poller.status

        if len(self._running) >= MAX_CONCURRENT_POLLERS:
            raise TooManyDraftsError(
                f"already following {len(self._running)} drafts, which is the "
                f"safe limit for one process against a per-IP rate limit"
            )

        poller = DraftPoller(
            self._settings,
            provider,
            self._registry.event_bus,
            binding,
            state=session.draft_state,
            sequence=self._registry.sequence,
            on_picks_applied=lambda: self._registry.publish_board_update(session),
            recorder=self._recorder,
        )
        task = asyncio.create_task(poller.run(), name=f"draft-poller-{binding.draft_id}")
        task.add_done_callback(lambda _: self._running.pop(binding.draft_id, None))

        self._running[binding.draft_id] = _Supervised(poller=poller, task=task, session=session)
        log.info("poller_started", draft_id=binding.draft_id, active=len(self._running))
        return poller.status

    async def stop(self, draft_id: str) -> bool:
        """Stop one poller and wait for it to finish. Returns whether it ran."""
        supervised = self._running.get(draft_id)
        if supervised is None:
            return False

        supervised.poller.request_stop()
        try:
            await asyncio.wait_for(asyncio.shield(supervised.task), timeout=10)
        except TimeoutError:
            # The loop is wedged somewhere it should not be. Cancelling is
            # correct: every applied pick is already in the session and written
            # to draft_picks, so nothing is lost by killing the task.
            log.warning("poller_stop_timed_out", draft_id=draft_id)
            supervised.task.cancel()
        self._running.pop(draft_id, None)
        log.info("poller_stopped", draft_id=draft_id)
        return True

    async def stop_all(self) -> None:
        """Stop every poller. Called on application shutdown."""
        for draft_id in list(self._running):
            await self.stop(draft_id)
