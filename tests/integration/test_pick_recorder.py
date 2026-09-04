"""Persisting the picks a live draft actually made.

Before this existed, picks lived only in the in-memory session and were
re-fetched from Sleeper on every restart. That is fine while Sleeper serves the
draft, and this project has already seen it return 404 for a completed one.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.api.services.league_connect import connect_sleeper_draft
from fhe.api.services.pick_recorder import DatabasePickRecorder
from fhe.core.draft.models import DraftPick
from fhe.db.models.draft import Draft, DraftPickRecord
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


def domain_pick(pick_no: int, player_uuid: str, **overrides: Any) -> DraftPick:
    """A pick as the poller hands it to the recorder."""
    return DraftPick(
        pick_no=pick_no,
        round_number=(pick_no - 1) // 12 + 1,
        draft_slot=((pick_no - 1) % 12) + 1,
        player_uuid=player_uuid,
        roster_id=overrides.get("roster_id", ((pick_no - 1) % 12) + 1),
        picked_by=overrides.get("picked_by", "someone"),
        source_player_id=overrides.get("source_player_id", f"s-{pick_no - 1}"),
    )


async def connect(
    session_factory: Any,
    registry: DraftSessionRegistry,
    picks: tuple[Any, ...] = (),
    recorder: Any = None,
) -> Any:
    """Connect a draft the way the API does."""
    return await connect_sleeper_draft(
        session_factory,
        FakeSleeper(make_league(), make_draft(), picks),
        registry,
        league_id=LEAGUE_ID,
        draft_id=DRAFT_ID,
        user_id=USER_ID,
        recorder=recorder,
    )


async def stored(session_factory: Any) -> list[DraftPickRecord]:
    """Every persisted pick, in draft order."""
    async with session_factory() as session:
        return list(
            (await session.execute(select(DraftPickRecord).order_by(DraftPickRecord.pick_no)))
            .scalars()
            .all()
        )


class TestRecording:
    async def test_picks_reach_the_database(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        await connect(session_factory, registry)
        recorder = DatabasePickRecorder(session_factory)

        written = await recorder.record(DRAFT_ID, [domain_pick(1, "p-000")])

        assert written == 1
        rows = await stored(session_factory)
        assert [(r.pick_no, r.player_uuid) for r in rows] == [(1, "p-000")]
        assert rows[0].round_number == 1
        assert rows[0].draft_slot == 1

    async def test_recording_the_same_pick_twice_converges(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """The poller re-observes every pick on every cycle by design."""
        await connect(session_factory, registry)
        recorder = DatabasePickRecorder(session_factory)

        await recorder.record(DRAFT_ID, [domain_pick(1, "p-000")])
        await recorder.record(DRAFT_ID, [domain_pick(1, "p-000")])

        assert len(await stored(session_factory)) == 1

    async def test_an_unresolved_player_is_stored_not_dropped(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """The placeholder identity is not a foreign key, but the pick is real.

        Keeping the provider's own id is what makes the row recoverable when
        identity resolution improves; discarding the pick would leave a hole in
        the draft that nothing could ever fill.
        """
        await connect(session_factory, registry)
        recorder = DatabasePickRecorder(session_factory)

        await recorder.record(
            DRAFT_ID,
            [domain_pick(1, "sleeper:99999", source_player_id="99999")],
        )

        rows = await stored(session_factory)
        assert rows[0].player_uuid is None
        assert rows[0].provider_player_id == "99999"

    async def test_several_unresolved_picks_all_survive(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """A null player_uuid must not collide with the once-per-draft rule.

        Nulls are distinct under a unique constraint, which is the behaviour
        wanted here and the opposite of the trap that made `week` need a
        sentinel elsewhere in the schema.
        """
        await connect(session_factory, registry)
        recorder = DatabasePickRecorder(session_factory)

        await recorder.record(
            DRAFT_ID,
            [
                domain_pick(1, "sleeper:1", source_player_id="1"),
                domain_pick(2, "sleeper:2", source_player_id="2"),
            ],
        )

        assert len(await stored(session_factory)) == 2

    async def test_the_draft_records_when_it_last_saw_a_pick(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        await connect(session_factory, registry)
        recorder = DatabasePickRecorder(session_factory)

        await recorder.record(DRAFT_ID, [domain_pick(1, "p-000")])

        async with session_factory() as session:
            draft = (
                await session.execute(select(Draft).where(Draft.provider_draft_id == DRAFT_ID))
            ).scalar_one()
            assert draft.last_pick_observed_at is not None


class TestFailureIsNeverFatal:
    async def test_an_unknown_draft_is_logged_not_raised(self, session_factory: Any) -> None:
        """No parent row means nothing can be written, and that is not a crash."""
        recorder = DatabasePickRecorder(session_factory)
        assert await recorder.record("no-such-draft", [domain_pick(1, "p-000")]) == 0

    async def test_a_broken_database_does_not_stop_the_draft(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """The rule that outranks the audit trail.

        Draft night is precisely when nobody can drop out to debug a database,
        so a write failure must cost the record and nothing else.
        """
        await connect(session_factory, registry)
        recorder = DatabasePickRecorder(session_factory)
        # Prime the draft-id cache so the failure lands on the write itself.
        await recorder.record(DRAFT_ID, [domain_pick(1, "p-000")])

        broken = domain_pick(2, "p-001")
        object.__setattr__(broken, "round_number", None)  # violates NOT NULL

        assert await recorder.record(DRAFT_ID, [broken]) == 0
        # The pick that did work is still there.
        assert len(await stored(session_factory)) == 1

    async def test_nothing_to_record_is_not_an_error(self, session_factory: Any) -> None:
        recorder = DatabasePickRecorder(session_factory)
        assert await recorder.record(DRAFT_ID, []) == 0


class TestBackfillOnConnect:
    async def test_picks_made_before_we_connected_are_recorded(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """Joining in the fourth round must not lose the first three.

        The poller records only what it newly applies, and by its first cycle
        these are duplicates, so without the backfill they would never land.
        """
        recorder = DatabasePickRecorder(session_factory)
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))

        await connect(session_factory, registry, picks, recorder=recorder)

        rows = await stored(session_factory)
        assert [r.pick_no for r in rows] == list(range(1, 13))
        assert all(r.player_uuid is not None for r in rows)

    async def test_connecting_twice_does_not_duplicate_the_backfill(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        recorder = DatabasePickRecorder(session_factory)
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 13))

        await connect(session_factory, registry, picks, recorder=recorder)
        await connect(session_factory, registry, picks, recorder=recorder)

        assert len(await stored(session_factory)) == 12
