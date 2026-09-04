"""Rebuild a live draft session after the API process restarts.

The problem this solves
-----------------------
Draft sessions live in memory. That is the right shape for a mock draft, which
is cheap to recreate from its seed. It is the wrong answer on draft night: if
the API restarts at pick 40, the war room should not go dark and require the
user to reconnect their league by hand while a clock is running.

Why it can be done safely
-------------------------
Nothing about a live session is *originated* here. Sleeper owns the picks, the
database owns the league and draft metadata and the canonical player pool, and
the recommendation engine is deterministic. A session is therefore a pure
function of facts that all survive a restart:

    (persisted league + draft) + (provider's current picks) + (player pool)

Recovery re-runs the same connection path a first connect runs, with the seat
read from the database instead of from a user id. It does not replay events,
does not re-apply stored picks on top of provider picks, and does not merge two
sources of truth — it asks Sleeper what has happened and rebuilds from that.
That is what makes it idempotent: running it twice converges, because the
provider's answer is the state.

What is deliberately *not* done
-------------------------------
Mock simulations are not recovered. They are seeded and ephemeral by design,
and a lost one costs a keystroke to recreate. Recovery is also per-process
rather than shared: two API workers would each rebuild their own copy on
demand, which is correct but wasteful, and is the point at which Redis-backed
shared state would start to earn its complexity. For the single-worker
deployment this product targets, it is unnecessary — see docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.api.services.draft_session import DraftSession, DraftSessionRegistry, SessionNotFoundError
from fhe.api.services.league_connect import (
    LeagueSource,
    connect_sleeper_draft,
)
from fhe.api.services.poller_manager import PollerManager, TooManyDraftsError
from fhe.core.types import DraftStatus
from fhe.db.models.draft import Draft, FantasyLeague
from fhe.observability import get_logger
from fhe.worker.draft_poller import DraftSource

log = get_logger(__name__)

SOURCE = "sleeper"

# One recovery at a time per draft. Without this, a browser reconnecting fires
# the board read, the state read, and the event stream at once, and three
# concurrent rebuilds would register three sessions and start three pollers
# against the same draft.
_locks: dict[str, asyncio.Lock] = {}


def _lock_for(draft_id: str) -> asyncio.Lock:
    """The recovery lock for one draft, created on first use."""
    lock = _locks.get(draft_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[draft_id] = lock
    return lock


class RecoverableDraft:
    """What the database remembers about a live draft, minus the session."""

    __slots__ = ("league_id", "provider_draft_id", "status", "user_draft_slot")

    def __init__(
        self,
        *,
        provider_draft_id: str,
        league_id: str,
        user_draft_slot: int | None,
        status: str,
    ) -> None:
        self.provider_draft_id = provider_draft_id
        self.league_id = league_id
        self.user_draft_slot = user_draft_slot
        self.status = status


async def find_recoverable(
    session_factory: async_sessionmaker[AsyncSession], draft_id: str
) -> RecoverableDraft | None:
    """Look up the persisted metadata for a live draft, if there is any.

    Returning ``None`` is the normal answer for a simulation id or a typo, and
    the caller turns that into the same 404 it would have raised anyway.
    """
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(
                        Draft.provider_draft_id,
                        Draft.user_draft_slot,
                        Draft.status,
                        FantasyLeague.provider_league_id,
                    )
                    .join(FantasyLeague, Draft.league_id == FantasyLeague.id)
                    .where(Draft.provider_draft_id == draft_id, Draft.source == SOURCE)
                )
            ).first()
    except SQLAlchemyError as error:
        # Recovery is best-effort, and its failure must degrade to "no such
        # session" rather than a 500. The case that makes this load-bearing is
        # the product's own zero-infrastructure demo path: a fresh SQLite file
        # with no migrations applied has no `drafts` table at all, and every
        # request for an unknown draft would otherwise return a server error
        # instead of an honest 404. Logged rather than swallowed, so a genuine
        # database outage is still visible.
        log.warning("recovery_lookup_failed", draft_id=draft_id, error=str(error))
        return None

    if row is None or row.provider_draft_id is None or row.provider_league_id is None:
        return None
    return RecoverableDraft(
        provider_draft_id=row.provider_draft_id,
        league_id=row.provider_league_id,
        user_draft_slot=row.user_draft_slot,
        status=row.status,
    )


async def recover_session(
    session_factory: async_sessionmaker[AsyncSession],
    sleeper: LeagueSource,
    registry: DraftSessionRegistry,
    *,
    draft_id: str,
    pollers: PollerManager | None = None,
    poller_provider_factory: Callable[[], DraftSource] | None = None,
    as_of: date | None = None,
) -> DraftSession | None:
    """Rebuild one live session, restarting its poller when appropriate.

    Returns ``None`` when the draft is not one this process can rebuild, which
    the caller should treat exactly like "no such session".
    """
    async with _lock_for(draft_id):
        # Another request may have finished recovery while this one waited.
        try:
            return registry.get(draft_id)
        except SessionNotFoundError:
            pass

        persisted = await find_recoverable(session_factory, draft_id)
        if persisted is None:
            return None

        connected, binding, session = await connect_sleeper_draft(
            session_factory,
            sleeper,
            registry,
            league_id=persisted.league_id,
            draft_id=persisted.provider_draft_id,
            user_draft_slot=persisted.user_draft_slot,
            as_of=as_of,
            recorder=pollers.recorder if pollers else None,
        )

        # A finished draft is history: rebuilding it is useful for review, but
        # starting a poller for it would burn rate limit observing nothing.
        if (
            pollers is not None
            and poller_provider_factory is not None
            and connected.draft_status is not DraftStatus.COMPLETE
            and draft_id not in pollers.active_draft_ids
        ):
            try:
                # Built here rather than passed in, so an id that turns out not
                # to be recoverable never constructs a client it will not use.
                pollers.start(poller_provider_factory(), binding, session)
            except TooManyDraftsError:
                # The board still works; it just will not update by itself.
                # Better than refusing to show a draft that is happening now.
                log.warning("recovery_poller_not_started", draft_id=draft_id, reason="at capacity")

        log.info(
            "live_session_recovered",
            draft_id=draft_id,
            picks_already_made=connected.picks_already_made,
            status=connected.draft_status.value,
            user_slot=connected.user_draft_slot,
        )
        return session
