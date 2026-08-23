"""Weekly production and snap-count ingestion.

Workload is what turns the health model's exposure and durability terms from
assumptions into measurements, and the two datasets join on *different*
identifiers — which is the interesting part.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sqlalchemy import func, select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.data.ingest.nflverse_workload import (
    MIN_PLAUSIBLE_SNAP_ROWS,
    MIN_PLAUSIBLE_STAT_ROWS,
    ingest_snap_counts,
    ingest_weekly_stats,
)
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.models.football import PlayerWeeklyStat, SnapCount
from fhe.db.models.player import Player, PlayerExternalId

pytestmark = pytest.mark.integration

SEASON = 2025
LINKED_GSIS = "00-0011111"
LINKED_PFR = "SmitJo00"


def stat_row(**overrides: Any) -> dict[str, Any]:
    """One nflverse weekly-stats row, with the verified column names."""
    row: dict[str, Any] = {
        "player_id": LINKED_GSIS,
        "player_display_name": "Linked Player",
        "position": "RB",
        "season": SEASON,
        "week": 1,
        "season_type": "REG",
        "team": "SEA",
        "opponent_team": "SF",
        "carries": 18.0,
        "targets": 3.0,
        "receptions": 2.0,
        "attempts": 0.0,
        "sacks_suffered": 0.0,
        "passing_yards": 0.0,
        "passing_tds": 0.0,
        "passing_interceptions": 0.0,
        "rushing_yards": 84.0,
        "rushing_tds": 1.0,
        "receiving_yards": 15.0,
        "receiving_tds": 0.0,
        "fumbles_lost": 0.0,
        "fantasy_points": 15.9,
        "fantasy_points_ppr": 17.9,
    }
    row.update(overrides)
    return row


def snap_row(**overrides: Any) -> dict[str, Any]:
    """One nflverse snap-counts row, keyed by pfr_player_id."""
    row: dict[str, Any] = {
        "season": SEASON,
        "game_type": "REG",
        "week": 1,
        "player": "Linked Player",
        "pfr_player_id": LINKED_PFR,
        "position": "RB",
        "team": "SEA",
        "offense_snaps": 48.0,
        "offense_pct": 0.72,
    }
    row.update(overrides)
    return row


def padded(rows: list[dict[str, Any]], *, to: int, pad: dict[str, Any]) -> pl.DataFrame:
    """Pad a frame with out-of-scope rows so it clears the plausibility floor."""
    out = list(rows)
    for index in range(max(0, to - len(rows))):
        out.append({**pad, "week": (index % 18) + 1})
    return pl.DataFrame(out)


class FakeNflverse:
    """Returns canned workload frames."""

    def __init__(self, stats: pl.DataFrame, snaps: pl.DataFrame) -> None:
        self._stats = stats
        self._snaps = snaps

    async def get_weekly_player_stats(self, season: int, **_: Any) -> pl.DataFrame:
        return self._stats

    async def get_snap_counts(self, season: int, **_: Any) -> pl.DataFrame:
        return self._snaps


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    async with factory() as session:
        session.add(
            Player(
                player_uuid="uuid-linked",
                full_name="Linked Player",
                normalized_name="linkedplayer",
                position="RB",
                team="SEA",
                is_active=True,
                identity_method="DIRECT_GSIS",
                identity_confidence=1.0,
                source="test",
            )
        )
        session.add_all(
            [
                PlayerExternalId(
                    player_uuid="uuid-linked", system="gsis_id", external_id=LINKED_GSIS
                ),
                PlayerExternalId(
                    player_uuid="uuid-linked", system="pfr_id", external_id=LINKED_PFR
                ),
            ]
        )
        await session.commit()

    yield factory
    await engine.dispose()


async def rows_of(session_factory: Any, model: Any) -> list[Any]:
    async with session_factory() as session:
        return list((await session.execute(select(model))).scalars().all())


class TestWeeklyStats:
    async def test_ingests_usage_and_production(self, session_factory: Any) -> None:
        frame = padded(
            [stat_row()],
            to=MIN_PLAUSIBLE_STAT_ROWS,
            pad=stat_row(player_id="00-0099999", position="T"),
        )
        await ingest_weekly_stats(
            session_factory, FakeNflverse(frame, pl.DataFrame([snap_row()])), SEASON
        )

        rows = await rows_of(session_factory, PlayerWeeklyStat)
        assert len(rows) == 1
        assert rows[0].carries == pytest.approx(18.0)
        assert rows[0].targets == pytest.approx(3.0)
        assert rows[0].fantasy_points_ppr == pytest.approx(17.9)

    async def test_half_ppr_is_derived_from_standard_and_receptions(
        self, session_factory: Any
    ) -> None:
        """The source publishes standard and full PPR; half is exactly between."""
        frame = padded(
            [stat_row()],
            to=MIN_PLAUSIBLE_STAT_ROWS,
            pad=stat_row(player_id="00-0099999", position="T"),
        )
        await ingest_weekly_stats(
            session_factory, FakeNflverse(frame, pl.DataFrame([snap_row()])), SEASON
        )
        rows = await rows_of(session_factory, PlayerWeeklyStat)
        assert rows[0].fantasy_points_half_ppr == pytest.approx(15.9 + 2 * 0.5)

    async def test_out_of_scope_players_are_not_identity_failures(
        self, session_factory: Any
    ) -> None:
        frame = padded(
            [stat_row()],
            to=MIN_PLAUSIBLE_STAT_ROWS,
            pad=stat_row(player_id="00-0099999", position="T"),
        )
        run = await ingest_weekly_stats(
            session_factory, FakeNflverse(frame, pl.DataFrame([snap_row()])), SEASON
        )
        assert run.rows_unresolved_identity == 0
        assert run.details["rows_out_of_scope"] > 0

    async def test_implausible_values_are_rejected(self, session_factory: Any) -> None:
        frame = padded(
            [stat_row(week=99)],
            to=MIN_PLAUSIBLE_STAT_ROWS,
            pad=stat_row(player_id="00-0099999", position="T"),
        )
        run = await ingest_weekly_stats(
            session_factory, FakeNflverse(frame, pl.DataFrame([snap_row()])), SEASON
        )
        assert any(r["reason"] == "implausible_week" for r in run.rejections)
        assert await rows_of(session_factory, PlayerWeeklyStat) == []

    async def test_a_truncated_season_aborts(self, session_factory: Any) -> None:
        with pytest.raises(ValueError, match="plausibility floor"):
            await ingest_weekly_stats(
                session_factory,
                FakeNflverse(pl.DataFrame([stat_row()]), pl.DataFrame([snap_row()])),
                SEASON,
            )

    async def test_is_idempotent(self, session_factory: Any) -> None:
        frame = padded(
            [stat_row(), stat_row(week=2)],
            to=MIN_PLAUSIBLE_STAT_ROWS,
            pad=stat_row(player_id="00-0099999", position="T"),
        )
        provider = FakeNflverse(frame, pl.DataFrame([snap_row()]))
        for _ in range(3):
            await ingest_weekly_stats(session_factory, provider, SEASON)

        async with session_factory() as session:
            count = (
                await session.execute(select(func.count()).select_from(PlayerWeeklyStat))
            ).scalar()
        assert count == 2


class TestSnapCounts:
    async def test_joins_on_pfr_id_not_gsis(self, session_factory: Any) -> None:
        """The only dataset here keyed on pfr_id, which is why the player sync
        harvests every identifier rather than only the ones then in use."""
        frame = padded(
            [snap_row()],
            to=MIN_PLAUSIBLE_SNAP_ROWS,
            pad=snap_row(pfr_player_id="UnknXX00", position="T"),
        )
        await ingest_snap_counts(
            session_factory, FakeNflverse(pl.DataFrame([stat_row()]), frame), SEASON
        )

        rows = await rows_of(session_factory, SnapCount)
        assert len(rows) == 1
        assert rows[0].player_uuid == "uuid-linked"
        assert rows[0].offense_snaps == 48
        assert rows[0].offense_snap_pct == pytest.approx(0.72)

    async def test_a_player_without_a_pfr_id_simply_has_no_snaps(
        self, session_factory: Any
    ) -> None:
        """Missing data, not zero usage — the health model treats them differently."""
        frame = padded(
            [snap_row(pfr_player_id="NoMapXX00", position="RB")],
            to=MIN_PLAUSIBLE_SNAP_ROWS,
            pad=snap_row(pfr_player_id="UnknXX00", position="T"),
        )
        run = await ingest_snap_counts(
            session_factory, FakeNflverse(pl.DataFrame([stat_row()]), frame), SEASON
        )
        assert run.rows_unresolved_identity >= 1
        assert await rows_of(session_factory, SnapCount) == []

    async def test_is_idempotent(self, session_factory: Any) -> None:
        frame = padded(
            [snap_row(), snap_row(week=2)],
            to=MIN_PLAUSIBLE_SNAP_ROWS,
            pad=snap_row(pfr_player_id="UnknXX00", position="T"),
        )
        provider = FakeNflverse(pl.DataFrame([stat_row()]), frame)
        for _ in range(3):
            await ingest_snap_counts(session_factory, provider, SEASON)

        async with session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(SnapCount))).scalar()
        assert count == 2
