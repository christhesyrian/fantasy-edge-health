"""Ingestion tests for nflverse injury reports.

Uses a synthetic in-memory dataframe rather than a downloaded season, so the
suite is offline, fast, and deterministic. The dataframe reproduces the real
schema and the real dirt: whitespace padding, "Note" values, laterality
prefixes, and non-injury reasons.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sqlalchemy import func, select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.data.ingest.nflverse_injuries import (
    MIN_PLAUSIBLE_ROWS_PER_SEASON,
    ingest_injuries_for_season,
)
from fhe.data.ingest.run import RunStatus
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.models.health import InjuryEvent, PracticeReport
from fhe.db.models.player import Player, PlayerExternalId

pytestmark = pytest.mark.integration

SEASON = 2025
LINKED_GSIS = "00-0011111"
UNLINKED_GSIS = "00-0099999"
LINEMAN_GSIS = "00-0077777"


class FakeNflverse:
    """Returns a canned injuries dataframe."""

    def __init__(self, frame: pl.DataFrame) -> None:
        self._frame = frame

    async def get_injuries(self, season: int, **_: Any) -> pl.DataFrame:
        """Return the canned frame."""
        return self._frame


def injury_row(**overrides: Any) -> dict[str, Any]:
    """One nflverse injuries row with the verified column set."""
    row: dict[str, Any] = {
        "season": SEASON,
        "game_type": "REG",
        "team": "SEA",
        "week": 1,
        "gsis_id": LINKED_GSIS,
        "position": "WR",
        "full_name": "Linked Player",
        "first_name": "Linked",
        "last_name": "Player",
        "report_primary_injury": "right Shoulder",
        "report_secondary_injury": None,
        "report_status": "Questionable",
        "practice_primary_injury": "right Shoulder",
        "practice_secondary_injury": None,
        "practice_status": "Limited Participation in Practice",
        "date_modified": datetime(2025, 9, 10, 12, 0, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def frame_of(rows: list[dict[str, Any]], *, pad_to: int = 0) -> pl.DataFrame:
    """Build a dataframe, padding with benign rows to clear the plausibility floor."""
    padded = list(rows)
    for index in range(max(0, pad_to - len(rows))):
        padded.append(
            injury_row(
                gsis_id=LINEMAN_GSIS,
                position="T",
                week=(index % 18) + 1,
                full_name=f"Lineman {index}",
            )
        )
    return pl.DataFrame(padded)


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    # One player who resolves, so gsis -> uuid linkage has something to hit.
    async with factory() as session:
        session.add(
            Player(
                player_uuid="uuid-linked",
                full_name="Linked Player",
                normalized_name="linkedplayer",
                position="WR",
                team="SEA",
                is_active=True,
                identity_method="DIRECT_GSIS",
                identity_confidence=1.0,
                source="test",
            )
        )
        session.add(
            PlayerExternalId(player_uuid="uuid-linked", system="gsis_id", external_id=LINKED_GSIS)
        )
        await session.commit()

    yield factory
    await engine.dispose()


async def rows_of(session_factory: Any, model: Any) -> list[Any]:
    """Fetch every row of a model."""
    async with session_factory() as session:
        return list((await session.execute(select(model))).scalars().all())


class TestNormalisation:
    async def test_normalises_region_while_keeping_raw_text(self, session_factory: Any) -> None:
        """A taxonomy bug must be fixable by re-running over stored rows."""
        frame = frame_of([injury_row()], pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON)
        await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)

        events = await rows_of(session_factory, InjuryEvent)
        assert len(events) == 1
        assert events[0].body_region == "SHOULDER"
        assert events[0].raw_primary_injury == "right Shoulder"
        assert events[0].designation == "QUESTIONABLE"
        assert events[0].raw_report_status == "Questionable"

    async def test_practice_and_designation_land_in_separate_tables(
        self, session_factory: Any
    ) -> None:
        """They answer different questions and must stay independently queryable."""
        frame = frame_of([injury_row()], pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON)
        await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)

        practice = await rows_of(session_factory, PracticeReport)
        assert len(practice) == 1
        assert practice[0].status == "LIMITED"
        assert practice[0].raw_status == "Limited Participation in Practice"

    async def test_whitespace_padding_does_not_become_a_practice_report(
        self, session_factory: Any
    ) -> None:
        """nflverse ships literal '\\n    ' rows; they are not participation."""
        frame = frame_of(
            [injury_row(practice_status="\n    ", report_status=None, report_primary_injury=None)],
            pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON,
        )
        await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)
        assert await rows_of(session_factory, PracticeReport) == []

    async def test_non_injury_reasons_are_classified_not_treated_as_injuries(
        self, session_factory: Any
    ) -> None:
        frame = frame_of(
            [injury_row(report_primary_injury="Not injury related - resting player")],
            pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON,
        )
        await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)
        events = await rows_of(session_factory, InjuryEvent)
        assert events[0].body_region == "REST"

    async def test_observed_at_carries_the_provider_timestamp(self, session_factory: Any) -> None:
        """The point-in-time anchor that keeps a training set leak-free."""
        frame = frame_of([injury_row()], pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON)
        await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)
        events = await rows_of(session_factory, InjuryEvent)
        assert events[0].observed_at is not None
        assert events[0].observed_at.year == 2025


class TestIdentityAccounting:
    async def test_out_of_scope_players_are_not_counted_as_identity_failures(
        self, session_factory: Any
    ) -> None:
        """Otherwise thousands of linemen would drown out the real failures."""
        frame = frame_of(
            [injury_row(), injury_row(gsis_id=LINEMAN_GSIS, position="T", week=2)],
            pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON,
        )
        run = await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)

        assert run.rows_unresolved_identity == 0
        assert run.details["rows_out_of_scope"] > 0

    async def test_an_unlinked_fantasy_player_is_counted_as_unresolved(
        self, session_factory: Any
    ) -> None:
        frame = frame_of(
            [injury_row(gsis_id=UNLINKED_GSIS, position="RB", full_name="Missing Back")],
            pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON,
        )
        run = await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)
        assert run.rows_unresolved_identity >= 1


class TestValidationAndSafety:
    async def test_implausible_week_is_rejected(self, session_factory: Any) -> None:
        frame = frame_of([injury_row(week=99)], pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON)
        run = await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)

        assert any(r["reason"] == "implausible_week" for r in run.rejections)
        assert await rows_of(session_factory, InjuryEvent) == []

    async def test_a_truncated_season_aborts_rather_than_overwriting(
        self, session_factory: Any
    ) -> None:
        """A short file is a provider incident, not a quiet season of no injuries."""
        with pytest.raises(ValueError, match="plausibility floor"):
            await ingest_injuries_for_season(
                session_factory, FakeNflverse(frame_of([injury_row()])), SEASON
            )

    async def test_a_failed_run_is_recorded(self, session_factory: Any) -> None:
        from fhe.db.models.pipeline import DataIngestionRun

        with pytest.raises(ValueError):
            await ingest_injuries_for_season(
                session_factory, FakeNflverse(frame_of([injury_row()])), SEASON
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

    async def test_duplicate_player_week_is_rejected_not_crashed(
        self, session_factory: Any
    ) -> None:
        """A provider repeating a player-week must not violate the constraint."""
        frame = frame_of([injury_row(), injury_row()], pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON)
        run = await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)

        assert any("duplicate_player_week" in r["reason"] for r in run.rejections)
        assert len(await rows_of(session_factory, InjuryEvent)) == 1

    async def test_is_idempotent(self, session_factory: Any) -> None:
        frame = frame_of(
            [injury_row(), injury_row(week=2, report_primary_injury="Knee")],
            pad_to=MIN_PLAUSIBLE_ROWS_PER_SEASON,
        )
        for _ in range(3):
            await ingest_injuries_for_season(session_factory, FakeNflverse(frame), SEASON)

        async with session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(InjuryEvent))).scalar()
        assert count == 2
