"""Data-quality checks.

Each test proves a check actually catches the problem it names. A check that
cannot fail is worse than no check, because it looks like coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.data.quality import (
    ALL_CHECKS,
    MIN_ACTIVE_PLAYERS,
    Severity,
    check_adp_is_in_range,
    check_ages_are_plausible,
    check_identity_coverage,
    check_player_pool_depth,
    check_positions_are_known,
    check_projections_are_in_range,
    check_weeks_are_plausible,
    run_quality_checks,
    summarise,
)
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.pipeline import DataQualityResult
from fhe.db.models.player import Player, PlayerExternalId

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def player(uuid: str, **kwargs: Any) -> Player:
    """A persisted player row."""
    return Player(
        player_uuid=uuid,
        full_name=kwargs.get("full_name", f"Player {uuid}"),
        normalized_name=uuid,
        position=kwargs.get("position", "RB"),
        team="SEA",
        age=kwargs.get("age", 26.0),
        is_active=kwargs.get("is_active", True),
        identity_method="DIRECT_GSIS",
        identity_confidence=1.0,
        source="test",
    )


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def seed_healthy(session_factory: Any, count: int = MIN_ACTIVE_PLAYERS) -> None:
    """A database that should pass every check."""
    async with session_factory() as session:
        for index in range(count):
            uuid = f"p-{index:04d}"
            session.add(player(uuid))
            session.add(
                PlayerExternalId(player_uuid=uuid, system="gsis_id", external_id=f"00-{index:07d}")
            )
        await session.commit()


class TestChecksCatchTheirProblem:
    async def test_pool_depth_fails_on_a_thin_database(self, session_factory: Any) -> None:
        await seed_healthy(session_factory, count=5)
        async with session_factory() as session:
            result = await check_player_pool_depth(session)

        assert not result.passed
        assert result.is_blocking
        assert "fhe ingest players" in result.message

    async def test_pool_depth_passes_when_deep_enough(self, session_factory: Any) -> None:
        await seed_healthy(session_factory)
        async with session_factory() as session:
            assert (await check_player_pool_depth(session)).passed

    async def test_unknown_position_is_caught(self, session_factory: Any) -> None:
        """An UNKNOWN position silently removes a player from every positional
        calculation without raising anything."""
        async with session_factory() as session:
            session.add(player("bad", position="UNKNOWN"))
            await session.commit()
            result = await check_positions_are_known(session)

        assert not result.passed
        assert result.observed == 1
        assert result.samples[0]["position"] == "UNKNOWN"

    @pytest.mark.parametrize("age", [3.0, 99.0])
    async def test_implausible_age_is_caught(self, session_factory: Any, age: float) -> None:
        async with session_factory() as session:
            session.add(player("bad", age=age))
            await session.commit()
            result = await check_ages_are_plausible(session)

        assert not result.passed
        assert result.samples[0]["age"] == pytest.approx(age)

    async def test_identity_coverage_never_exceeds_one(self, session_factory: Any) -> None:
        """Regression: counting gsis rows across all players while dividing by
        the active subset reported 102% coverage."""
        async with session_factory() as session:
            session.add(player("active-1"))
            session.add(player("retired-1", is_active=False))
            session.add(player("retired-2", is_active=False))
            for uuid in ("active-1", "retired-1", "retired-2"):
                session.add(
                    PlayerExternalId(player_uuid=uuid, system="gsis_id", external_id=f"g-{uuid}")
                )
            await session.commit()
            result = await check_identity_coverage(session)

        assert result.observed <= 1.0
        assert result.observed == pytest.approx(1.0)

    async def test_identity_coverage_warns_when_linkage_collapses(
        self, session_factory: Any
    ) -> None:
        """A silent failure: the board still renders, but health loses evidence."""
        async with session_factory() as session:
            for index in range(10):
                session.add(player(f"p-{index}"))
            await session.commit()
            result = await check_identity_coverage(session)

        assert not result.passed
        assert result.severity is Severity.WARNING
        assert "crosswalk" in result.message

    async def test_implausible_week_is_caught(self, session_factory: Any) -> None:
        from fhe.db.models.health import InjuryEvent

        async with session_factory() as session:
            session.add(player("p1"))
            session.add(
                InjuryEvent(
                    player_uuid="p1",
                    season=2025,
                    week=99,
                    body_region="KNEE",
                    designation="OUT",
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
            await session.commit()
            result = await check_weeks_are_plausible(session)

        assert not result.passed
        assert result.samples[0]["table"] == "injury_events"

    async def test_season_long_sentinel_is_a_valid_week(self, session_factory: Any) -> None:
        """Week 0 is the deliberate sentinel, not bad data."""
        async with session_factory() as session:
            session.add(player("p1"))
            session.add(
                FantasyProjection(
                    player_uuid="p1",
                    season=2026,
                    week=SEASON_LONG_WEEK,
                    scoring_format="ppr",
                    projected_points=280.0,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
            await session.commit()
            assert (await check_weeks_are_plausible(session)).passed

    @pytest.mark.parametrize("adp", [0.0, -5.0, 900.0])
    async def test_out_of_range_adp_is_caught(self, session_factory: Any, adp: float) -> None:
        async with session_factory() as session:
            session.add(player("p1"))
            session.add(
                AdpSnapshot(
                    player_uuid="p1",
                    season=2026,
                    scoring_format="ppr",
                    adp=adp,
                    snapshot_date=NOW,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
            await session.commit()
            result = await check_adp_is_in_range(session)

        assert not result.passed

    async def test_out_of_range_projection_is_caught(self, session_factory: Any) -> None:
        async with session_factory() as session:
            session.add(player("p1"))
            session.add(
                FantasyProjection(
                    player_uuid="p1",
                    season=2026,
                    week=SEASON_LONG_WEEK,
                    scoring_format="ppr",
                    projected_points=40_000.0,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
            await session.commit()
            result = await check_projections_are_in_range(session)

        assert not result.passed
        assert result.samples[0]["projected_points"] == pytest.approx(40_000.0)


class TestRun:
    async def test_persists_every_result(self, session_factory: Any) -> None:
        await seed_healthy(session_factory)
        results = await run_quality_checks(session_factory)

        assert len(results) == len(ALL_CHECKS)
        async with session_factory() as session:
            rows = (await session.execute(select(DataQualityResult))).scalars().all()
        assert len(rows) == len(ALL_CHECKS)
        assert {row.check_name for row in rows} == {r.name for r in results}

    async def test_a_healthy_database_has_no_blocking_failures(self, session_factory: Any) -> None:
        await seed_healthy(session_factory)
        results = await run_quality_checks(session_factory)
        assert not [r for r in results if r.is_blocking]

    async def test_failures_are_recorded_not_raised(self, session_factory: Any) -> None:
        """A quality run reports on the whole dataset; aborting at the first
        problem would hide the rest."""
        async with session_factory() as session:
            session.add(player("bad", position="UNKNOWN", age=200.0))
            await session.commit()

        results = await run_quality_checks(session_factory)
        assert len(results) == len(ALL_CHECKS)
        assert len([r for r in results if not r.passed]) >= 2

    async def test_summary_marks_blocking_failures_distinctly(self, session_factory: Any) -> None:
        async with session_factory() as session:
            session.add(player("bad", position="UNKNOWN"))
            await session.commit()

        text = summarise(await run_quality_checks(session_factory))
        assert "[FAIL]" in text
        assert "[ok  ]" in text
