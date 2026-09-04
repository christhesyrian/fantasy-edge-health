"""Ingest the current depth chart, which says who is actually going to play.

Why only the latest chart
-------------------------
The provider publishes a scrape roughly daily: 496,713 rows for 2026 across 168
snapshots. Storing every one would be a time series nobody reads, and the table
holds one observation per player per week, so it could not hold them anyway.

What a draft needs is the *current* answer to "is he the starter", so this reads
the newest snapshot and stores it at the season-long sentinel, the same way a
season projection is stored. Re-running replaces it, which is the intended
behaviour: a depth chart is a statement about now, not a historical record.

The provider gives no week number, only the timestamp of the scrape. Deriving a
week from that date would be inventing a fact the source never supplied, so the
timestamp is carried through as ``observed_at`` and the week stays the sentinel.

Which column is the depth position
----------------------------------
``pos_rank`` is the rank within the position group and is what "RB2" means.
``pos_slot`` is the alignment in the formation - two receivers can share slot 1
while ranking first and fourth - and is not a depth order at all. Reading the
wrong one would have quietly ranked half a receiving corps as starters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Protocol

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.data.identity import clean_token
from fhe.data.ingest.lookup import load_external_id_map
from fhe.data.ingest.run import IngestionRunRecorder, ingestion_run
from fhe.db.base import SEASON_LONG_WEEK, utcnow
from fhe.db.models.football import DepthChartSnapshot
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME: Final = "nflverse"
DATASET: Final = "depth_charts"

# Positions a fantasy draft reasons about. The file is mostly defence and
# special teams, which are charted per alignment and tell a fantasy manager
# nothing: a team defence is drafted whole.
FANTASY_POSITIONS: Final[frozenset[str]] = frozenset({"QB", "RB", "WR", "TE", "FB"})

# Fullbacks are charted separately and drafted as running backs.
_POSITION_ALIASES: Final[dict[str, str]] = {"FB": "RB"}

# Fantasy-position rows a whole league-wide chart carries: the real 2026
# snapshot has 582 across 32 teams. Well below this is a truncated or
# half-published file, and writing one would silently demote every player it
# omitted - which reads exactly like a real benching.
#
# Counted on what the provider sent, before identity resolution, so that a
# broken crosswalk is not mistaken for a truncated file. A crosswalk failure
# leaves rows unresolved, which is already counted and sampled per rejection,
# and writes nothing rather than corrupting anything.
MIN_PLAUSIBLE_ROWS: Final = 400


class DepthChartSource(Protocol):
    """The one capability this job needs from a provider."""

    async def get_depth_charts(self, season: int, *, force_refresh: bool = False) -> pl.DataFrame:
        """Every depth-chart observation the provider holds for a season."""
        ...


def _parse_observed_at(raw: Any) -> datetime | None:
    """The scrape timestamp, or None if the provider's format changed."""
    text = clean_token(raw)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


async def ingest_depth_charts(
    session_factory: async_sessionmaker[AsyncSession],
    provider: DepthChartSource,
    season: int,
    *,
    force_refresh: bool = False,
) -> IngestionRunRecorder:
    """Load the most recent depth chart for one season.

    Idempotent: one row per player per season at the season-long sentinel, so
    re-running replaces the chart rather than accumulating snapshots.
    """
    async with ingestion_run(session_factory, provider=PROVIDER_NAME, dataset=DATASET) as run:
        frame = await provider.get_depth_charts(season, force_refresh=force_refresh)
        if frame.height == 0:
            log.warning("depth_chart_empty", season=season)
            return run

        latest_dt = frame["dt"].max()
        # Sorted so that when one player is charted twice - a fullback listed at
        # both FB and RB, a receiver at two alignments - the row kept below is
        # his best listing rather than whichever the file happened to emit first.
        current = frame.filter(pl.col("dt") == latest_dt).sort("pos_rank", nulls_last=True)
        run.read(current.height)

        async with session_factory() as session:
            gsis_to_uuid = await load_external_id_map(session, "gsis_id")

        now = utcnow()
        observed_at = _parse_observed_at(latest_dt) or now
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        fantasy_rows = 0

        for record in current.iter_rows(named=True):
            position = (clean_token(record.get("pos_abb")) or "").upper()
            if position not in FANTASY_POSITIONS:
                continue
            fantasy_rows += 1

            gsis_id = clean_token(record.get("gsis_id"))
            if gsis_id is None:
                run.reject("no_gsis_id", player=record.get("player_name"))
                continue
            player_uuid = gsis_to_uuid.get(gsis_id)
            if player_uuid is None:
                run.reject("unresolved_player", gsis_id=gsis_id)
                continue

            rank = record.get("pos_rank")
            if rank is None:
                run.reject("no_depth_rank", gsis_id=gsis_id)
                continue

            if player_uuid in seen:
                # A fullback charted at both FB and RB, or a receiver listed at
                # two alignments. The uniqueness key holds one row per player,
                # and the rows are sorted so the one already kept is his best.
                run.reject("duplicate_player", gsis_id=gsis_id)
                continue
            seen.add(player_uuid)

            rows.append(
                {
                    "player_uuid": player_uuid,
                    "season": season,
                    "week": SEASON_LONG_WEEK,
                    "team": (clean_token(record.get("team")) or "").upper() or None,
                    "depth_position": _POSITION_ALIASES.get(position, position),
                    "depth_order": int(rank),
                    "source": PROVIDER_NAME,
                    "ingested_at": now,
                    "observed_at": observed_at,
                    "source_updated_at": observed_at,
                }
            )

        if 0 < fantasy_rows < MIN_PLAUSIBLE_ROWS:
            raise ValueError(
                f"depth chart for {season} carries only {fantasy_rows} fantasy-position "
                f"rows, below the plausibility floor of {MIN_PLAUSIBLE_ROWS}; refusing "
                "rather than overwriting a good chart with a partial one"
            )

        if rows:
            async with session_factory() as session:
                await upsert_rows(
                    session,
                    DepthChartSnapshot,
                    rows,
                    conflict_columns=["player_uuid", "season", "week", "source"],
                )
                await session.commit()
            run.wrote(len(rows))

        log.info(
            "depth_charts_ingested",
            season=season,
            observed_at=observed_at.isoformat(),
            players=len(rows),
            fantasy_rows=fantasy_rows,
        )
        return run
