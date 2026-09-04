"""Ingest the game schedule, which is what makes strength of schedule possible.

The schedule answers "who does this team play, and when". Paired with what each
defence actually allowed — which this system already measures from its own
weekly stats — it answers the question a season projection cannot: whether a
player's *playoff* weeks are soft or brutal.

Why the whole file rather than one season
-----------------------------------------
nflverse ships every season in a single `games` asset, so this reads it once and
filters. The upcoming season is present as soon as the league publishes
fixtures, with scores still empty — which is exactly the state a draft needs and
the reason this job does not require a season to have been played.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.data.identity import clean_token
from fhe.data.ingest.run import IngestionRunRecorder, ingestion_run
from fhe.db.base import utcnow
from fhe.db.models.football import ScheduledGame
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME: Final = "nflverse"
DATASET: Final = "schedule"

# A regular season is 272 games in a 32-team, 17-game league. Anything much
# below this for a season the provider claims to cover is a truncated file, and
# refusing beats overwriting a good schedule with half of one.
MIN_PLAUSIBLE_REGULAR_GAMES: Final = 200

MIN_WEEK: Final = 1
MAX_WEEK: Final = 23


class ScheduleSource(Protocol):
    """The one capability this job needs from a provider."""

    async def get_schedules(self, *, force_refresh: bool = False) -> pl.DataFrame:
        """Every scheduled game the provider publishes."""
        ...


def _int_or_none(value: Any) -> int | None:
    """An integer, or None for the nulls a future game legitimately carries."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row(record: dict[str, Any], now: Any) -> dict[str, Any] | None:
    """Map one provider row, or None when it is unusable."""
    game_id = clean_token(record.get("game_id"))
    home = clean_token(record.get("home_team"))
    away = clean_token(record.get("away_team"))
    season = _int_or_none(record.get("season"))
    week = _int_or_none(record.get("week"))
    if not game_id or not home or not away or season is None or week is None:
        return None
    if not MIN_WEEK <= week <= MAX_WEEK:
        return None
    return {
        "provider_game_id": game_id,
        "season": season,
        "week": week,
        "game_type": clean_token(record.get("game_type")) or "REG",
        "home_team": home.upper(),
        "away_team": away.upper(),
        "home_score": _int_or_none(record.get("home_score")),
        "away_score": _int_or_none(record.get("away_score")),
        "source": PROVIDER_NAME,
        "ingested_at": now,
        # The fact this row describes is the game, so the date it is scheduled
        # for is what makes it point-in-time reconstructable.
        "observed_at": now,
        "source_updated_at": None,
    }


async def ingest_schedule(
    session_factory: async_sessionmaker[AsyncSession],
    provider: ScheduleSource,
    seasons: list[int],
    *,
    force_refresh: bool = False,
) -> IngestionRunRecorder:
    """Load the schedule for the given seasons.

    Idempotent: keyed on the provider's own game id, so re-running converges and
    a schedule change (a flexed game) updates in place rather than duplicating.
    """
    async with ingestion_run(session_factory, provider=PROVIDER_NAME, dataset=DATASET) as run:
        frame = await provider.get_schedules(force_refresh=force_refresh)
        wanted = frame.filter(pl.col("season").is_in(seasons))
        run.read(len(wanted))

        rows: list[dict[str, Any]] = []
        now = utcnow()
        for record in wanted.iter_rows(named=True):
            mapped = _row(record, now)
            if mapped is None:
                run.reject("unusable_row", game_id=record.get("game_id"))
                continue
            rows.append(mapped)

        for season in seasons:
            regular = sum(1 for r in rows if r["season"] == season and r["game_type"] == "REG")
            if 0 < regular < MIN_PLAUSIBLE_REGULAR_GAMES:
                # Some games for this season, but not a season's worth. That is
                # a truncated file, and writing it would leave a schedule with
                # silent holes that strength of schedule would read as easy.
                raise ValueError(
                    f"schedule for {season} has only {regular} regular-season games, "
                    f"below the plausibility floor of {MIN_PLAUSIBLE_REGULAR_GAMES}; "
                    "refusing rather than storing a partial schedule"
                )

        if rows:
            async with session_factory() as session:
                await upsert_rows(
                    session,
                    ScheduledGame,
                    rows,
                    conflict_columns=["provider_game_id", "source"],
                )
                await session.commit()
            run.wrote(len(rows))

        log.info("schedule_ingested", seasons=seasons, games=len(rows))
        return run
