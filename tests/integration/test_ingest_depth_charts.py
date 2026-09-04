"""Depth-chart ingestion.

The provider publishes a scrape roughly daily rather than a weekly record, so
the interesting parts are choosing the newest snapshot, reading the right column
as the depth order, and refusing a half-published file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sqlalchemy import select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.data.ingest.nflverse_depth_charts import (
    MIN_PLAUSIBLE_ROWS,
    ingest_depth_charts,
)
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.football import DepthChartSnapshot
from fhe.db.models.player import Player, PlayerExternalId

pytestmark = pytest.mark.integration

SEASON = 2026
OLD = "2026-03-14T07:32:09Z"
NEW = "2026-09-04T11:57:41Z"


def chart_row(**overrides: Any) -> dict[str, Any]:
    """One nflverse depth-chart row, with the verified column names."""
    row: dict[str, Any] = {
        "dt": NEW,
        "team": "SEA",
        "player_name": "Player 0",
        "espn_id": "1",
        "gsis_id": "00-0000000",
        "pos_grp_id": "1",
        "pos_grp": "3WR 1TE",
        "pos_id": "1",
        "pos_name": "Running Back",
        "pos_abb": "RB",
        "pos_slot": 11,
        "pos_rank": 1,
    }
    row.update(overrides)
    return row


def padded(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Pad to a whole league's worth of chart so the frame clears the floor.

    The padding players are deliberately not in the database: the floor asks
    whether the *provider* sent a whole chart, so unresolvable rows must still
    count toward it. They are rejected individually, which is the mechanism for
    an identity gap.
    """
    out = list(rows)
    for index in range(MIN_PLAUSIBLE_ROWS):
        out.append(
            chart_row(
                gsis_id=f"00-99{index:05d}",
                player_name=f"Unknown {index}",
                pos_rank=index + 5,
            )
        )
    return pl.DataFrame(out)


class FakeNflverse:
    """Returns a canned depth-chart frame."""

    def __init__(self, frame: pl.DataFrame) -> None:
        self._frame = frame

    async def get_depth_charts(self, season: int, **_: Any) -> pl.DataFrame:
        return self._frame


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    """A database holding four resolvable players."""
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    async with factory() as session:
        for index in range(4):
            session.add(
                Player(
                    player_uuid=f"uuid-{index}",
                    full_name=f"Player {index}",
                    normalized_name=f"player{index}",
                    position="RB",
                    team="SEA",
                    is_active=True,
                    identity_method="DIRECT_GSIS",
                    identity_confidence=1.0,
                    source="test",
                )
            )
            session.add(
                PlayerExternalId(
                    player_uuid=f"uuid-{index}",
                    system="gsis_id",
                    external_id=f"00-000000{index}",
                )
            )
        await session.commit()

    yield factory
    await engine.dispose()


async def stored(session_factory: Any) -> list[DepthChartSnapshot]:
    """Every persisted listing, in depth order."""
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(DepthChartSnapshot).order_by(DepthChartSnapshot.depth_order)
                )
            )
            .scalars()
            .all()
        )


class TestIngestion:
    async def test_only_the_newest_snapshot_is_stored(self, session_factory: Any) -> None:
        """168 scrapes a season is a time series nobody reads.

        A draft wants the current answer to "is he the starter", and the table
        holds one observation per player anyway.
        """
        frame = padded(
            [
                chart_row(dt=OLD, gsis_id="00-0000000", pos_rank=1),
                chart_row(dt=OLD, gsis_id="00-0000001", pos_rank=2),
                chart_row(dt=NEW, gsis_id="00-0000000", pos_rank=2),
                chart_row(dt=NEW, gsis_id="00-0000001", pos_rank=1),
            ]
        )
        await ingest_depth_charts(session_factory, FakeNflverse(frame), SEASON)

        rows = await stored(session_factory)
        by_uuid = {r.player_uuid: r.depth_order for r in rows}
        assert by_uuid == {"uuid-0": 2, "uuid-1": 1}

    async def test_the_depth_order_comes_from_the_rank_not_the_alignment(
        self, session_factory: Any
    ) -> None:
        """pos_slot is where a player lines up, and two receivers can share one.

        Reading it as a depth order would have ranked half a receiving corps as
        starters.
        """
        frame = padded(
            [
                chart_row(gsis_id="00-0000000", pos_abb="WR", pos_slot=1, pos_rank=1),
                chart_row(gsis_id="00-0000001", pos_abb="WR", pos_slot=1, pos_rank=4),
            ]
        )
        await ingest_depth_charts(session_factory, FakeNflverse(frame), SEASON)

        assert {r.player_uuid: r.depth_order for r in await stored(session_factory)} == {
            "uuid-0": 1,
            "uuid-1": 4,
        }

    async def test_the_chart_is_stored_at_the_season_long_sentinel(
        self, session_factory: Any
    ) -> None:
        """The provider gives a scrape timestamp and no week.

        Deriving a week from that date would invent a fact the source never
        supplied, so the timestamp is carried as observed_at instead.
        """
        await ingest_depth_charts(session_factory, FakeNflverse(padded([chart_row()])), SEASON)

        row = (await stored(session_factory))[0]
        assert row.week == SEASON_LONG_WEEK
        assert row.observed_at is not None
        assert row.observed_at.year == 2026
        assert row.observed_at.month == 9

    async def test_re_running_replaces_rather_than_accumulates(self, session_factory: Any) -> None:
        """A depth chart is a statement about now, not a historical record."""
        await ingest_depth_charts(
            session_factory,
            FakeNflverse(padded([chart_row(pos_rank=3)])),
            SEASON,
        )
        await ingest_depth_charts(
            session_factory,
            FakeNflverse(padded([chart_row(pos_rank=1)])),
            SEASON,
        )

        rows = await stored(session_factory)
        assert len(rows) == 1
        assert rows[0].depth_order == 1

    async def test_a_fullback_is_charted_as_a_running_back(self, session_factory: Any) -> None:
        """Fullbacks are listed separately and drafted as backs."""
        frame = padded([chart_row(pos_abb="FB", pos_name="Fullback")])
        await ingest_depth_charts(session_factory, FakeNflverse(frame), SEASON)

        assert (await stored(session_factory))[0].depth_position == "RB"

    async def test_defensive_positions_are_left_out(self, session_factory: Any) -> None:
        """The file is mostly defence, which a fantasy draft takes whole."""
        await ingest_depth_charts(session_factory, FakeNflverse(padded([chart_row()])), SEASON)
        assert len(await stored(session_factory)) == 1


class TestRefusal:
    async def test_a_truncated_chart_is_refused_not_written(self, session_factory: Any) -> None:
        """A corrupt response must never overwrite good state.

        Writing a half-published chart would silently demote every player it
        omitted, which reads exactly like a real benching.
        """
        frame = pl.DataFrame([chart_row()])

        with pytest.raises(ValueError, match="plausibility floor"):
            await ingest_depth_charts(session_factory, FakeNflverse(frame), SEASON)

    async def test_a_broken_crosswalk_is_not_mistaken_for_a_truncated_file(
        self, session_factory: Any
    ) -> None:
        """A whole chart nobody can be resolved from writes nothing, quietly.

        Refusing here would blame the provider for our own identity gap, and
        the rejections already record what could not be matched. Nothing is
        written either way, so the existing chart survives.
        """
        frame = padded([])
        run = await ingest_depth_charts(session_factory, FakeNflverse(frame), SEASON)

        assert run.rows_written == 0
        assert run.rows_rejected >= MIN_PLAUSIBLE_ROWS
        assert await stored(session_factory) == []

    async def test_an_empty_response_writes_nothing_and_does_not_raise(
        self, session_factory: Any
    ) -> None:
        """A season with no chart yet is normal, not a failure."""
        empty = pl.DataFrame(schema=pl.DataFrame([chart_row()]).schema)
        run = await ingest_depth_charts(session_factory, FakeNflverse(empty), SEASON)

        assert run.rows_written == 0
        assert await stored(session_factory) == []

    async def test_an_unresolvable_player_is_counted_not_dropped_silently(
        self, session_factory: Any
    ) -> None:
        frame = padded([chart_row(gsis_id="00-0004040", player_name="Nobody")])
        run = await ingest_depth_charts(session_factory, FakeNflverse(frame), SEASON)

        assert run.rows_rejected >= 1
