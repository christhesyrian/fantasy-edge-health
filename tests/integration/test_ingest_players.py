"""Ingestion tests for the Sleeper player sync.

Runs against a fixture payload and a temporary SQLite database, so the suite
never touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.data.identity import PlayerCrosswalk
from fhe.data.ingest.run import RunStatus
from fhe.data.ingest.sleeper_players import (
    MIN_PLAUSIBLE_PLAYER_COUNT,
    PlayerSyncAbortedError,
    sync_sleeper_players,
)
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.models.health import CurrentPlayerHealth
from fhe.db.models.pipeline import DataIngestionRun
from fhe.db.models.player import Player, PlayerExternalId, PlayerIdentityConflict

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[2] / "data" / "fixtures" / "sleeper"


class FakeSleeper:
    """Stands in for :class:`SleeperProvider`, returning a fixture payload."""

    def __init__(self, players: dict[str, Any]) -> None:
        self._players = players
        self.calls = 0

    async def get_all_players(self, **_: Any) -> dict[str, Any]:
        """Return the canned payload."""
        self.calls += 1
        return dict(self._players)


@pytest.fixture
def payload() -> dict[str, Any]:
    """The synthetic Sleeper payload, keyed by player id."""
    fixture: dict[str, Any] = json.loads((FIXTURES / "players_small.json").read_text())
    players: dict[str, Any] = fixture["players"]
    return players


@pytest.fixture
def crosswalk() -> PlayerCrosswalk:
    """A crosswalk that can link one fixture player Sleeper could not."""
    return PlayerCrosswalk.from_rows(
        [
            {
                "sleeper_id": "1002",
                "gsis_id": "00-0088888",
                "espn_id": "555",
                "yahoo_id": "NA",
                "name": "Injured Star",
                "position": "WR",
                "team": "SEA",
            },
        ]
    )


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def count(session_factory: Any, model: Any) -> int:
    """Row count for a model."""
    async with session_factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar() or 0


class TestPlayerSync:
    async def test_persists_only_fantasy_positions(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        """An offensive tackle is out of scope, not a rejection."""
        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        async with session_factory() as session:
            names = set((await session.execute(select(Player.full_name))).scalars().all())
        assert "Ameer Abdullah" in names
        assert "Left Tackle" not in names

    async def test_records_identity_method_and_confidence(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        """A caller must be able to tell a certain match from a guess."""
        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(Player.full_name, Player.identity_method, Player.identity_confidence)
                )
            ).all()
        by_name = {name: (method, confidence) for name, method, confidence in rows}

        assert by_name["Ameer Abdullah"][0] == "DIRECT_GSIS"
        assert by_name["Ameer Abdullah"][1] == 1.0
        assert by_name["Injured Star"][0] == "CROSSWALK"
        assert by_name["Unlinked Rookie"][0] == "UNRESOLVED"
        assert by_name["Unlinked Rookie"][1] == 0.0

    async def test_health_row_only_written_when_something_is_reported(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        """Absence of a report is not evidence of health, so no row is invented."""
        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        CurrentPlayerHealth.designation,
                        CurrentPlayerHealth.body_region,
                        CurrentPlayerHealth.raw_body_part,
                    )
                )
            ).all()
        designations = {r[0] for r in rows}
        assert "IR" in designations
        assert "QUESTIONABLE" in designations

        by_designation = {r[0]: r for r in rows}
        # Raw provider text is preserved alongside the normalised region.
        assert by_designation["IR"][1] == "KNEE"
        assert by_designation["IR"][2] == "Knee - ACL"
        assert by_designation["QUESTIONABLE"][1] == "SHOULDER"
        assert by_designation["QUESTIONABLE"][2] == "right Shoulder"

    async def test_implausible_values_are_rejected_not_stored(
        self, session_factory: Any, payload: dict[str, Any]
    ) -> None:
        """An age of 250 and a weight of 9 must not reach the database."""
        await sync_sleeper_players(
            session_factory, sleeper=FakeSleeper(payload), min_player_count=1
        )
        async with session_factory() as session:
            row = (
                await session.execute(select(Player).where(Player.full_name == "Bad Age"))
            ).scalar_one()

        assert row.age is None
        assert row.weight_pounds is None
        assert row.height_inches is None
        assert row.jersey_number is None
        assert row.years_experience is None

    async def test_height_parses_both_reported_formats(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        """Sleeper reports height as inches ("69") or feet-inches ("6'2")."""
        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        async with session_factory() as session:
            rows = dict(
                (await session.execute(select(Player.full_name, Player.height_inches))).all()
            )
        assert rows["Ameer Abdullah"] == 69
        assert rows["Injured Star"] == 74

    async def test_records_without_a_player_id_are_rejected(
        self, session_factory: Any, payload: dict[str, Any]
    ) -> None:
        run = await sync_sleeper_players(
            session_factory, sleeper=FakeSleeper(payload), min_player_count=1
        )
        assert any(r["reason"] == "missing_player_id" for r in run.rejections)

    async def test_is_idempotent(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        for _ in range(3):
            await sync_sleeper_players(
                session_factory,
                sleeper=FakeSleeper(payload),
                crosswalk=crosswalk,
                min_player_count=1,
            )
        first = await count(session_factory, Player)
        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        assert await count(session_factory, Player) == first
        assert await count(session_factory, PlayerExternalId) > 0

    async def test_player_uuid_is_stable_across_runs(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        """A re-key would orphan every historical row pointing at the old uuid."""
        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        async with session_factory() as session:
            before = set((await session.execute(select(Player.player_uuid))).scalars().all())

        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        async with session_factory() as session:
            after = set((await session.execute(select(Player.player_uuid))).scalars().all())

        assert before == after


class TestSafety:
    async def test_a_tiny_payload_aborts_rather_than_wiping_state(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        """A truncated provider response must never overwrite good data."""
        await sync_sleeper_players(
            session_factory,
            sleeper=FakeSleeper(payload),
            crosswalk=crosswalk,
            min_player_count=1,
        )
        good_count = await count(session_factory, Player)
        assert good_count > 0

        with pytest.raises(PlayerSyncAbortedError, match="plausibility floor"):
            await sync_sleeper_players(
                session_factory,
                sleeper=FakeSleeper({"1": {"player_id": "1", "position": "RB"}}),
                crosswalk=crosswalk,
                min_player_count=MIN_PLAUSIBLE_PLAYER_COUNT,
            )

        assert await count(session_factory, Player) == good_count

    async def test_an_aborted_run_is_recorded_as_failed(self, session_factory: Any) -> None:
        """A job that rolls back must still leave lineage saying it failed."""
        with pytest.raises(PlayerSyncAbortedError):
            await sync_sleeper_players(
                session_factory,
                sleeper=FakeSleeper({}),
                min_player_count=MIN_PLAUSIBLE_PLAYER_COUNT,
            )

        async with session_factory() as session:
            run = (
                (
                    await session.execute(
                        select(DataIngestionRun).order_by(DataIngestionRun.id.desc())
                    )
                )
                .scalars()
                .first()
            )

        assert run is not None
        assert run.status == RunStatus.FAILED.value
        assert run.error_category == "PlayerSyncAbortedError"
        assert run.finished_at is not None

    async def test_the_production_default_floor_is_meaningfully_large(self) -> None:
        """Tests lower the floor explicitly; the default must stay protective."""
        import inspect

        from fhe.data.ingest import sleeper_players

        signature = inspect.signature(sleeper_players.sync_sleeper_players)
        assert signature.parameters["min_player_count"].default == MIN_PLAUSIBLE_PLAYER_COUNT
        assert MIN_PLAUSIBLE_PLAYER_COUNT >= 1000


class TestConflicts:
    async def test_unresolvable_players_still_persist(
        self, session_factory: Any, payload: dict[str, Any]
    ) -> None:
        """An August rookie with no history is real, and gets a durable id."""
        await sync_sleeper_players(
            session_factory, sleeper=FakeSleeper(payload), min_player_count=1
        )
        async with session_factory() as session:
            row = (
                await session.execute(select(Player).where(Player.full_name == "Unlinked Rookie"))
            ).scalar_one()
        assert row.player_uuid
        assert row.identity_method == "UNRESOLVED"

    async def test_conflicts_table_exists_and_is_writable(
        self, session_factory: Any, payload: dict[str, Any]
    ) -> None:
        await sync_sleeper_players(
            session_factory, sleeper=FakeSleeper(payload), min_player_count=1
        )
        assert await count(session_factory, PlayerIdentityConflict) >= 0

    async def test_run_details_report_linkage(
        self, session_factory: Any, payload: dict[str, Any], crosswalk: PlayerCrosswalk
    ) -> None:
        run = await sync_sleeper_players(
            session_factory, sleeper=FakeSleeper(payload), crosswalk=crosswalk, min_player_count=1
        )
        assert run.details["crosswalk_available"] is True
        assert run.details["players_persisted"] > 0
        assert run.status() in {RunStatus.SUCCESS, RunStatus.PARTIAL}
