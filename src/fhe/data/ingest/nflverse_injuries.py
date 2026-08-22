"""Ingest nflverse injury reports into ``injury_events`` and ``practice_reports``.

Verified coverage is 2009-2025 (see ``docs/DATA_SOURCES.md``). The 2026 season
has no file yet, which is expected rather than a failure.

Two normalisations happen here, both from :mod:`fhe.core.injury`:

* the free-text body part becomes a controlled :class:`BodyRegion`, and
* the practice string becomes a :class:`PracticeStatus`.

**The raw text is stored alongside both.** A taxonomy bug must be fixable by
re-running normalisation over stored rows, which is impossible if the original
string was discarded.

Game status and practice participation are written to *separate tables* because
they answer different questions. "Questionable after three full practices" and
"Questionable after three DNPs" are the same designation and very different
signals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final, Protocol

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.core.injury import (
    normalize_body_region,
    normalize_designation,
    normalize_practice_status,
)
from fhe.core.types import BodyRegion, Position, PracticeStatus
from fhe.data.identity import clean_token
from fhe.data.ingest.lookup import load_external_id_map
from fhe.data.ingest.run import IngestionRunRecorder, ingestion_run
from fhe.db.base import utcnow
from fhe.db.models.health import InjuryEvent, PracticeReport
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME: Final = "nflverse"
DATASET: Final = "injuries"

# A season file with fewer rows than this is not a real NFL season. Refusing
# beats overwriting a good season with a truncated one.
MIN_PLAUSIBLE_ROWS_PER_SEASON: Final = 500

# Bounds for a plausible NFL week, including the postseason weeks nflverse uses.
MIN_WEEK: Final = 1
MAX_WEEK: Final = 23

# Positions this product stores. An injury report for an offensive lineman is
# deliberately out of scope, and must be counted separately from a genuine
# identity failure - otherwise ~4,000 out-of-scope rows per season would drown
# out the handful of real resolution problems the metric exists to surface.
IN_SCOPE_POSITIONS: Final[frozenset[Position]] = frozenset(
    {Position.QB, Position.RB, Position.WR, Position.TE, Position.K}
)


class InjurySource(Protocol):
    """The only capability this job needs from an nflverse client."""

    async def get_injuries(self, season: int, *, force_refresh: bool = ...) -> pl.DataFrame:
        """Return one season of injury reports."""
        ...


def _as_int(value: Any) -> int | None:
    """Best-effort integer conversion for a dataframe cell."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _observed_at(row: dict[str, Any]) -> datetime | None:
    """When the provider says the report was last modified.

    This is the point-in-time anchor. Storing it is what lets a training set be
    rebuilt as of a given date without leaking information from the future.
    """
    value = row.get("date_modified")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


async def ingest_injuries_for_season(
    session_factory: async_sessionmaker[AsyncSession],
    provider: InjurySource,
    season: int,
    *,
    force_refresh: bool = False,
) -> IngestionRunRecorder:
    """Ingest one season of injury reports.

    Rows whose player cannot be resolved to an internal uuid are counted and
    sampled, never silently dropped: a season where identity resolution
    collapses must be visible in the run record.
    """
    async with ingestion_run(
        session_factory,
        provider=PROVIDER_NAME,
        dataset=DATASET,
        requested_resource=f"injuries_{season}",
    ) as run:
        frame = await provider.get_injuries(season, force_refresh=force_refresh)
        run.read(frame.height)

        if frame.height < MIN_PLAUSIBLE_ROWS_PER_SEASON:
            raise ValueError(
                f"injuries_{season} returned {frame.height} rows, below the "
                f"{MIN_PLAUSIBLE_ROWS_PER_SEASON} plausibility floor; refusing to "
                "overwrite known-good injury history"
            )

        async with session_factory() as session:
            gsis_to_uuid = await load_external_id_map(session, "gsis_id")

        injury_rows: list[dict[str, Any]] = []
        practice_rows: list[dict[str, Any]] = []
        # (player_uuid, season, week) already emitted, so a provider that
        # repeats a player-week does not fail the unique constraint.
        seen_injury: set[tuple[str, int, int | None]] = set()
        seen_practice: set[tuple[str, int, int | None]] = set()
        out_of_scope = 0
        now = utcnow()

        for row in frame.iter_rows(named=True):
            gsis_id = clean_token(row.get("gsis_id"))
            if gsis_id is None:
                run.reject("missing_gsis_id", name=clean_token(row.get("full_name")))
                continue

            player_uuid = gsis_to_uuid.get(gsis_id)
            if player_uuid is None:
                position = Position.parse(clean_token(row.get("position")))
                if position in IN_SCOPE_POSITIONS:
                    # A fantasy-relevant player we failed to link. This is the
                    # signal the metric exists for.
                    run.unresolved_identity()
                else:
                    out_of_scope += 1
                continue

            week = _as_int(row.get("week"))
            if week is not None and not (MIN_WEEK <= week <= MAX_WEEK):
                run.reject("implausible_week", gsis_id=gsis_id, week=week, season=season)
                continue

            raw_primary = clean_token(row.get("report_primary_injury"))
            raw_secondary = clean_token(row.get("report_secondary_injury"))
            raw_status = clean_token(row.get("report_status"))
            raw_practice = clean_token(row.get("practice_status"))
            raw_practice_injury = clean_token(row.get("practice_primary_injury"))
            observed = _observed_at(row)

            # --- game designation ---------------------------------------
            if raw_status or raw_primary:
                key = (player_uuid, season, week)
                if key in seen_injury:
                    run.reject(
                        "duplicate_player_week_injury",
                        gsis_id=gsis_id,
                        season=season,
                        week=week,
                    )
                else:
                    seen_injury.add(key)
                    region = normalize_body_region(raw_primary)
                    secondary = normalize_body_region(raw_secondary) if raw_secondary else None
                    injury_rows.append(
                        {
                            "player_uuid": player_uuid,
                            "season": season,
                            "week": week,
                            "game_type": clean_token(row.get("game_type")),
                            "body_region": region.value,
                            "secondary_region": (
                                secondary.value
                                if secondary and secondary is not BodyRegion.OTHER_UNKNOWN
                                else None
                            ),
                            "raw_primary_injury": raw_primary,
                            "raw_secondary_injury": raw_secondary,
                            "designation": normalize_designation(raw_status).value,
                            "raw_report_status": raw_status,
                            "games_missed": None,
                            "source": PROVIDER_NAME,
                            "source_updated_at": observed,
                            "ingested_at": now,
                            "observed_at": observed,
                        }
                    )

            # --- practice participation ----------------------------------
            status = normalize_practice_status(raw_practice)
            if status is not PracticeStatus.UNKNOWN:
                key = (player_uuid, season, week)
                if key in seen_practice:
                    run.reject(
                        "duplicate_player_week_practice",
                        gsis_id=gsis_id,
                        season=season,
                        week=week,
                    )
                else:
                    seen_practice.add(key)
                    practice_region = (
                        normalize_body_region(raw_practice_injury) if raw_practice_injury else None
                    )
                    practice_rows.append(
                        {
                            "player_uuid": player_uuid,
                            "season": season,
                            "week": week,
                            "report_date": observed.date() if observed else None,
                            "status": status.value,
                            "raw_status": raw_practice,
                            "body_region": (practice_region.value if practice_region else None),
                            "raw_injury": raw_practice_injury,
                            "source": PROVIDER_NAME,
                            "source_updated_at": observed,
                            "ingested_at": now,
                            "observed_at": observed,
                        }
                    )

        async with session_factory() as session:
            written = await upsert_rows(
                session,
                InjuryEvent,
                injury_rows,
                conflict_columns=["player_uuid", "season", "week", "source"],
            )
            written += await upsert_rows(
                session,
                PracticeReport,
                practice_rows,
                conflict_columns=["player_uuid", "season", "week", "source"],
            )
            await session.commit()

        run.wrote(written)
        run.details.update(
            {
                "season": season,
                "injury_events": len(injury_rows),
                "practice_reports": len(practice_rows),
                "players_in_scope": len(gsis_to_uuid),
                "rows_out_of_scope": out_of_scope,
            }
        )

    return run


async def ingest_injury_history(
    session_factory: async_sessionmaker[AsyncSession],
    provider: InjurySource,
    seasons: list[int],
    *,
    force_refresh: bool = False,
) -> list[IngestionRunRecorder]:
    """Ingest several seasons, one run per season.

    Seasons are separate runs deliberately: a failure in 2017 should not hide
    the fact that 2024 succeeded, and per-season lineage is what makes a partial
    backfill diagnosable.
    """
    runs: list[IngestionRunRecorder] = []
    for season in sorted(seasons):
        runs.append(
            await ingest_injuries_for_season(
                session_factory, provider, season, force_refresh=force_refresh
            )
        )
    return runs
