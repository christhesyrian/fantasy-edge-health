"""Durable storage for the picks a live draft actually made.

Until this existed, a live draft's picks lived only in the in-memory session and
were re-fetched from Sleeper whenever the API restarted. That works right up
until it doesn't: Sleeper has already returned 404 for one completed draft in
this project's own testing, and a league can be deleted or made private while
its draft is still the thing you want to look back at. `draft_picks` was
declared, with the uniqueness constraint its docstring calls "the database-level
guarantee behind the idempotency the poller relies on", and nothing ever wrote
to it.

Two rules govern everything here:

* **The live board outranks the record.** Every failure is caught and logged.
  Losing the audit trail is bad; freezing a war room mid-draft because a write
  timed out is unforgivable, and a draft is exactly the moment when nobody can
  afford to debug a database.
* **A pick is never dropped for being unrecognisable.** A player we could not
  match is stored with a null ``player_uuid`` and the provider's own id beside
  it, so the row is still there when the crosswalk improves.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.core.draft.models import DraftPick, is_unresolved_player
from fhe.db.base import utcnow
from fhe.db.models.draft import Draft, DraftPickRecord
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger

log = get_logger(__name__)

SOURCE = "sleeper"


class DatabasePickRecorder:
    """Writes applied picks to ``draft_picks``, idempotently and unfatally.

    Args:
        session_factory: Opens its own session per write. The recorder is called
            from a long-lived poller task, which has no request-scoped session
            and must not hold one open between polls.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        # The internal id of a provider draft never changes, so it is worth
        # remembering rather than re-querying on every pick.
        self._draft_row_ids: dict[str, int] = {}

    async def record(self, draft_id: str, picks: Sequence[DraftPick]) -> int:
        """Store picks for one draft. Returns how many rows were written.

        Never raises. A draft in progress must survive a database that is not.
        """
        if not picks:
            return 0
        try:
            async with self._session_factory() as session:
                row_id = await self._draft_row_id(session, draft_id)
                if row_id is None:
                    # Connecting persists the draft before any pick is applied,
                    # so this means the row was deleted underneath a running
                    # poller. Nothing useful can be written against no parent.
                    log.warning("pick_record_no_draft_row", draft_id=draft_id)
                    return 0
                written = await self._write(session, row_id, picks)
                await session.commit()
        except SQLAlchemyError as exc:
            # Deliberately broad within SQLAlchemy: every database failure mode
            # has the same correct response here, which is to keep drafting.
            log.warning(
                "pick_record_failed",
                draft_id=draft_id,
                picks=len(picks),
                error=str(exc),
            )
            return 0

        log.info("picks_recorded", draft_id=draft_id, picks=written)
        return written

    async def _draft_row_id(self, session: AsyncSession, draft_id: str) -> int | None:
        """Internal id for a provider draft id, cached after the first lookup."""
        cached = self._draft_row_ids.get(draft_id)
        if cached is not None:
            return cached
        row_id = (
            await session.execute(
                select(Draft.id).where(
                    Draft.provider_draft_id == draft_id,
                    Draft.source == SOURCE,
                )
            )
        ).scalar_one_or_none()
        if row_id is not None:
            self._draft_row_ids[draft_id] = row_id
        return row_id

    async def _write(
        self, session: AsyncSession, row_id: int, picks: Sequence[DraftPick]
    ) -> int:
        """Upsert the picks and advance the draft's last-pick marker."""
        now = utcnow()
        rows = [
            {
                "draft_id": row_id,
                "pick_no": pick.pick_no,
                "round_number": pick.round_number,
                "draft_slot": pick.draft_slot,
                "roster_id": pick.roster_id,
                # A placeholder identity is not a foreign key into players. It
                # becomes NULL, and the provider id below is what makes the row
                # recoverable once identity resolution improves.
                "player_uuid": (
                    None if is_unresolved_player(pick.player_uuid) else pick.player_uuid
                ),
                "provider_player_id": pick.source_player_id,
                "picked_by": pick.picked_by,
                "is_keeper": pick.is_keeper,
                "metadata_payload": None,
                "source": SOURCE,
                "ingested_at": now,
                "observed_at": pick.observed_at or now,
                "source_updated_at": None,
            }
            for pick in picks
        ]
        written = await upsert_rows(
            session,
            DraftPickRecord,
            rows,
            conflict_columns=["draft_id", "pick_no"],
        )
        latest = max((p.observed_at for p in picks if p.observed_at), default=now)
        await session.execute(
            update(Draft).where(Draft.id == row_id).values(last_pick_observed_at=latest)
        )
        return written
