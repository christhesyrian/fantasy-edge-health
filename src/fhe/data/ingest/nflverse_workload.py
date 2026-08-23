"""Ingest weekly production and snap counts.

Workload is what turns the health model's exposure and durability terms from
guesses into measurements. Until this runs, every availability assessment is
computed with ``workload=None`` and correctly reports lower confidence.

Two datasets, two different join keys
-------------------------------------
* Weekly stats are keyed by ``player_id``, which holds a ``gsis_id``.
* Snap counts are keyed by ``pfr_player_id``.

Nothing else in this project joins on ``pfr_id``, and discovering that at
ingestion time rather than assuming a shared key is why the crosswalk stores
every identifier it can rather than only the ones currently needed.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.core.types import Position
from fhe.data.identity import clean_token
from fhe.data.ingest.lookup import load_external_id_map
from fhe.data.ingest.run import IngestionRunRecorder, ingestion_run
from fhe.db.base import utcnow
from fhe.db.models.football import PlayerWeeklyStat, SnapCount
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME: Final = "nflverse"

# A season with fewer rows than this is not a real season. Refusing beats
# overwriting good history with a truncated file.
MIN_PLAUSIBLE_STAT_ROWS: Final = 2000
MIN_PLAUSIBLE_SNAP_ROWS: Final = 2000

MIN_WEEK: Final = 1
MAX_WEEK: Final = 23

# Positions this product stores. Counted separately from identity failures so a
# lineman's snap count does not look like a broken crosswalk.
IN_SCOPE_POSITIONS: Final[frozenset[Position]] = frozenset(
    {Position.QB, Position.RB, Position.WR, Position.TE, Position.K}
)

# Bounds beyond which a value is a parsing error rather than a performance.
MAX_PLAUSIBLE_SNAPS: Final = 120
MAX_PLAUSIBLE_TOUCHES: Final = 70


class WeeklyStatsSource(Protocol):
    """The capabilities this job needs from an nflverse client."""

    async def get_weekly_player_stats(
        self, season: int, *, force_refresh: bool = ...
    ) -> pl.DataFrame:
        """One season of weekly player statistics."""
        ...

    async def get_snap_counts(self, season: int, *, force_refresh: bool = ...) -> pl.DataFrame:
        """One season of per-game snap counts."""
        ...


def _number(value: Any, *, low: float = 0.0, high: float = 1e6) -> float | None:
    """Parse a bounded numeric cell, rejecting implausible values."""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN, which parquet uses for a missing float
        return None
    return parsed if low <= parsed <= high else None


def _week(value: Any) -> int | None:
    """Parse a week within the plausible range."""
    number = _number(value, low=MIN_WEEK, high=MAX_WEEK)
    return int(number) if number is not None else None


async def ingest_weekly_stats(
    session_factory: async_sessionmaker[AsyncSession],
    provider: WeeklyStatsSource,
    season: int,
    *,
    force_refresh: bool = False,
) -> IngestionRunRecorder:
    """Ingest one season of weekly player production."""
    async with ingestion_run(
        session_factory,
        provider=PROVIDER_NAME,
        dataset="weekly_stats",
        requested_resource=f"stats_player_week_{season}",
    ) as run:
        frame = await provider.get_weekly_player_stats(season, force_refresh=force_refresh)
        run.read(frame.height)

        if frame.height < MIN_PLAUSIBLE_STAT_ROWS:
            raise ValueError(
                f"stats_player_week_{season} returned {frame.height} rows, below the "
                f"{MIN_PLAUSIBLE_STAT_ROWS} plausibility floor; refusing to "
                "overwrite known-good history"
            )

        async with session_factory() as session:
            gsis_to_uuid = await load_external_id_map(session, "gsis_id")

        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        out_of_scope = 0
        now = utcnow()

        for record in frame.iter_rows(named=True):
            gsis_id = clean_token(record.get("player_id"))
            if gsis_id is None:
                run.reject("missing_player_id")
                continue

            player_uuid = gsis_to_uuid.get(gsis_id)
            if player_uuid is None:
                position = Position.parse(clean_token(record.get("position")))
                if position in IN_SCOPE_POSITIONS:
                    run.unresolved_identity()
                else:
                    out_of_scope += 1
                continue

            week = _week(record.get("week"))
            if week is None:
                run.reject("implausible_week", gsis_id=gsis_id, week=record.get("week"))
                continue

            key = (player_uuid, week)
            if key in seen:
                run.reject("duplicate_player_week", gsis_id=gsis_id, week=week)
                continue
            seen.add(key)

            rows.append(
                {
                    "player_uuid": player_uuid,
                    "season": season,
                    "week": week,
                    "season_type": clean_token(record.get("season_type")),
                    "team": clean_token(record.get("team")),
                    "opponent": clean_token(record.get("opponent_team")),
                    "carries": _number(record.get("carries"), high=MAX_PLAUSIBLE_TOUCHES),
                    "targets": _number(record.get("targets"), high=MAX_PLAUSIBLE_TOUCHES),
                    "receptions": _number(record.get("receptions"), high=MAX_PLAUSIBLE_TOUCHES),
                    "pass_attempts": _number(record.get("attempts"), high=100),
                    "sacks_taken": _number(record.get("sacks_suffered"), high=20),
                    "passing_yards": _number(record.get("passing_yards"), low=-100, high=800),
                    "passing_tds": _number(record.get("passing_tds"), high=12),
                    "interceptions": _number(record.get("passing_interceptions"), high=12),
                    "rushing_yards": _number(record.get("rushing_yards"), low=-100, high=400),
                    "rushing_tds": _number(record.get("rushing_tds"), high=8),
                    "receiving_yards": _number(record.get("receiving_yards"), low=-100, high=400),
                    "receiving_tds": _number(record.get("receiving_tds"), high=8),
                    "fumbles_lost": _number(record.get("fumbles_lost"), high=8),
                    "fantasy_points_standard": _number(
                        record.get("fantasy_points"), low=-20, high=100
                    ),
                    "fantasy_points_half_ppr": None,
                    "fantasy_points_ppr": _number(
                        record.get("fantasy_points_ppr"), low=-20, high=100
                    ),
                    "source": PROVIDER_NAME,
                    "source_updated_at": None,
                    "ingested_at": now,
                    "observed_at": now,
                }
            )

        # Half-PPR is derived, not published: it is standard plus half a point
        # per reception, which is exactly what the format means.
        for row in rows:
            standard = row["fantasy_points_standard"]
            receptions = row["receptions"]
            if standard is not None and receptions is not None:
                row["fantasy_points_half_ppr"] = round(standard + receptions * 0.5, 2)

        async with session_factory() as session:
            written = await upsert_rows(
                session,
                PlayerWeeklyStat,
                rows,
                conflict_columns=["player_uuid", "season", "week", "source"],
            )
            await session.commit()

        run.wrote(written)
        run.details.update(
            {
                "season": season,
                "rows_out_of_scope": out_of_scope,
                "players_in_scope": len(gsis_to_uuid),
            }
        )

    return run


async def ingest_snap_counts(
    session_factory: async_sessionmaker[AsyncSession],
    provider: WeeklyStatsSource,
    season: int,
    *,
    force_refresh: bool = False,
) -> IngestionRunRecorder:
    """Ingest one season of per-game snap counts.

    Joins on ``pfr_player_id``, which is the only dataset here that does. A
    player without a ``pfr_id`` in the crosswalk simply has no snap history,
    which the health model handles as missing data rather than as zero usage.
    """
    async with ingestion_run(
        session_factory,
        provider=PROVIDER_NAME,
        dataset="snap_counts",
        requested_resource=f"snap_counts_{season}",
    ) as run:
        frame = await provider.get_snap_counts(season, force_refresh=force_refresh)
        run.read(frame.height)

        if frame.height < MIN_PLAUSIBLE_SNAP_ROWS:
            raise ValueError(
                f"snap_counts_{season} returned {frame.height} rows, below the "
                f"{MIN_PLAUSIBLE_SNAP_ROWS} plausibility floor; refusing to "
                "overwrite known-good history"
            )

        async with session_factory() as session:
            pfr_to_uuid = await load_external_id_map(session, "pfr_id")

        if not pfr_to_uuid:
            log.warning(
                "no_pfr_id_mapping",
                impact="snap counts cannot be linked; run the player sync with a "
                "crosswalk available",
            )

        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        out_of_scope = 0
        now = utcnow()

        for record in frame.iter_rows(named=True):
            pfr_id = clean_token(record.get("pfr_player_id"))
            if pfr_id is None:
                run.reject("missing_pfr_player_id")
                continue

            player_uuid = pfr_to_uuid.get(pfr_id)
            if player_uuid is None:
                position = Position.parse(clean_token(record.get("position")))
                if position in IN_SCOPE_POSITIONS:
                    run.unresolved_identity()
                else:
                    out_of_scope += 1
                continue

            week = _week(record.get("week"))
            if week is None:
                run.reject("implausible_week", pfr_id=pfr_id, week=record.get("week"))
                continue

            key = (player_uuid, week)
            if key in seen:
                run.reject("duplicate_player_week", pfr_id=pfr_id, week=week)
                continue
            seen.add(key)

            snaps = _number(record.get("offense_snaps"), high=MAX_PLAUSIBLE_SNAPS)
            rows.append(
                {
                    "player_uuid": player_uuid,
                    "season": season,
                    "week": week,
                    "team": clean_token(record.get("team")),
                    "offense_snaps": int(snaps) if snaps is not None else None,
                    "offense_snap_pct": _number(record.get("offense_pct"), high=1.0),
                    "source": PROVIDER_NAME,
                    "source_updated_at": None,
                    "ingested_at": now,
                    "observed_at": now,
                }
            )

        async with session_factory() as session:
            written = await upsert_rows(
                session,
                SnapCount,
                rows,
                conflict_columns=["player_uuid", "season", "week", "source"],
            )
            await session.commit()

        run.wrote(written)
        run.details.update(
            {
                "season": season,
                "rows_out_of_scope": out_of_scope,
                "players_with_pfr_id": len(pfr_to_uuid),
            }
        )

    return run


async def ingest_workload_for_season(
    session_factory: async_sessionmaker[AsyncSession],
    provider: WeeklyStatsSource,
    season: int,
    *,
    force_refresh: bool = False,
) -> tuple[IngestionRunRecorder, IngestionRunRecorder]:
    """Ingest both workload datasets for a season, as separate runs.

    Separate runs on purpose: snap counts joining on a different identifier
    means they can fail independently, and a single combined run would hide
    which half broke.
    """
    stats = await ingest_weekly_stats(
        session_factory, provider, season, force_refresh=force_refresh
    )
    snaps = await ingest_snap_counts(session_factory, provider, season, force_refresh=force_refresh)
    return stats, snaps


__all__ = [
    "WeeklyStatsSource",
    "ingest_snap_counts",
    "ingest_weekly_stats",
    "ingest_workload_for_season",
]
