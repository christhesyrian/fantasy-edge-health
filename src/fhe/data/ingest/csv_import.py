"""Manual CSV import for ADP and projections.

This path exists so the product is useful with **no paid API and no scraping**.
The directive forbids scraping FantasyPros, ESPN, Yahoo or Rotowire in violation
of their terms, and no free licensed projection API has been verified, so the
supported route is a file the user is licensed to have.

Safety properties, because this is the one ingestion path fed by an uploaded
file rather than a provider we control:

* **Bounded input.** Byte and row limits are enforced before parsing, so an
  oversized or malicious file cannot exhaust memory.
* **No dynamic evaluation.** Values are parsed with explicit converters. Nothing
  is ``eval``-ed, and no formula is interpreted.
* **Bounded values.** A number outside a plausible range is rejected, because a
  mis-parsed column is far more likely than a genuine outlier.
* **Ambiguity is rejected, never guessed.** A row matching two players is
  reported, not assigned to whichever happened to be first.

The accepted column schemas are documented in ``data/schemas/README.md``.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from io import StringIO
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.core.types import Position, ScoringFormat
from fhe.data.identity import clean_token, normalize_name
from fhe.data.ingest.run import IngestionRunRecorder, ingestion_run
from fhe.db.base import SEASON_LONG_WEEK, utcnow
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.player import Player, PlayerExternalId
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger

log = get_logger(__name__)

# Upload guards. A season of projections is a few thousand rows; anything past
# these limits is a mistake or an attack, not data.
MAX_UPLOAD_BYTES: Final = 8 * 1024 * 1024
MAX_ROWS: Final = 20_000

# Plausible value ranges. Outside these, the column was almost certainly
# misread - a projection of 40,000 is a parsing bug, not a breakout season.
# Exclusive lower bound: an ADP of 0 is not "picked first", it is a blank or
# mis-parsed cell.
MIN_ADP_EXCLUSIVE: Final = 0.0
MAX_ADP: Final = 600.0
MIN_PROJECTED_POINTS: Final = -50.0
MAX_PROJECTED_POINTS: Final = 700.0
MIN_WEEK: Final = 1
MAX_WEEK: Final = 23


@unique
class ImportKind(StrEnum):
    """Which dataset a file carries."""

    ADP = "adp"
    PROJECTIONS = "projections"


REQUIRED_COLUMNS: Final[dict[ImportKind, frozenset[str]]] = {
    ImportKind.ADP: frozenset({"player_name", "position", "adp"}),
    ImportKind.PROJECTIONS: frozenset({"player_name", "position", "projected_points"}),
}


class CsvImportError(ValueError):
    """The file could not be accepted at all."""


@dataclass(frozen=True, slots=True)
class PlayerMatcher:
    """Resolves an imported row to an internal player.

    Built once per import from a single query, because a per-row lookup would
    turn a 3,000-row file into 3,000 round trips.
    """

    by_external_id: Mapping[tuple[str, str], str]
    by_name_position_team: Mapping[tuple[str, str, str], tuple[str, ...]]
    by_name_position: Mapping[tuple[str, str], tuple[str, ...]]

    @classmethod
    async def build(cls, session: AsyncSession) -> PlayerMatcher:
        """Index every persisted player for matching."""
        external: dict[tuple[str, str], str] = {}
        rows = await session.execute(
            select(
                PlayerExternalId.system,
                PlayerExternalId.external_id,
                PlayerExternalId.player_uuid,
            ).where(PlayerExternalId.system.in_(["sleeper_id", "gsis_id"]))
        )
        for system, external_id, player_uuid in rows.all():
            external[(system, external_id)] = player_uuid

        name_pos_team: dict[tuple[str, str, str], list[str]] = {}
        name_pos: dict[tuple[str, str], list[str]] = {}
        players = await session.execute(
            select(Player.player_uuid, Player.normalized_name, Player.position, Player.team)
        )
        for player_uuid, normalized, position, team in players.all():
            if not normalized:
                continue
            name_pos.setdefault((normalized, position), []).append(player_uuid)
            if team:
                name_pos_team.setdefault((normalized, position, team.upper()), []).append(
                    player_uuid
                )

        return cls(
            by_external_id=external,
            by_name_position_team={k: tuple(v) for k, v in name_pos_team.items()},
            by_name_position={k: tuple(v) for k, v in name_pos.items()},
        )

    def match(self, row: Mapping[str, Any]) -> tuple[str | None, str]:
        """Resolve one row to a player uuid.

        Returns:
            ``(player_uuid, reason)``. ``player_uuid`` is ``None`` when the row
            could not be matched unambiguously, and ``reason`` always explains
            which route was taken or why it failed.
        """
        for system in ("sleeper_id", "gsis_id"):
            value = clean_token(row.get(system))
            if value is not None:
                found = self.by_external_id.get((system, value))
                if found:
                    return found, f"matched_by_{system}"
                return None, f"unknown_{system}"

        name = normalize_name(clean_token(row.get("player_name")))
        position = Position.parse(clean_token(row.get("position")))
        if not name or position is Position.UNKNOWN:
            return None, "missing_name_or_position"

        team = (clean_token(row.get("team")) or "").upper()
        if team:
            candidates = self.by_name_position_team.get((name, position.value, team), ())
            if len(candidates) == 1:
                return candidates[0], "matched_by_name_position_team"
            if len(candidates) > 1:
                return None, "ambiguous_name_position_team"

        candidates = self.by_name_position.get((name, position.value), ())
        if len(candidates) == 1:
            return candidates[0], "matched_by_name_position"
        if len(candidates) > 1:
            return None, "ambiguous_name_position"
        return None, "no_matching_player"


def _bounded_float(
    raw: Any, *, low: float, high: float, exclusive_low: bool = False
) -> tuple[float | None, str | None]:
    """Parse a float within bounds, returning ``(value, error)``.

    Args:
        raw: The cell value.
        low: Lower bound.
        high: Upper bound.
        exclusive_low: Treat ``low`` as exclusive. ADP uses this, because an ADP
            of exactly 0 is a blank or mis-parsed cell rather than a real value.
    """
    text = clean_token(raw)
    if text is None:
        return None, "missing"
    try:
        value = float(text.replace(",", ""))
    except ValueError:
        return None, "not_a_number"
    below = value <= low if exclusive_low else value < low
    if below or value > high:
        return None, f"outside_bounds_{low}_{high}"
    return value, None


def _optional_float(raw: Any, *, low: float, high: float) -> float | None:
    """Parse an optional float, discarding out-of-range values."""
    value, error = _bounded_float(raw, low=low, high=high)
    return None if error else value


def _optional_int(raw: Any, *, low: int, high: int) -> int | None:
    """Parse an optional bounded integer."""
    text = clean_token(raw)
    if text is None:
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if low <= value <= high else None


def read_csv_text(text: str, kind: ImportKind) -> list[dict[str, str]]:
    """Parse and validate the structure of an uploaded CSV.

    Raises:
        CsvImportError: If the file is too large, has no header, is missing a
            required column, or exceeds the row limit.
    """
    encoded_size = len(text.encode("utf-8"))
    if encoded_size > MAX_UPLOAD_BYTES:
        raise CsvImportError(
            f"file is {encoded_size} bytes, above the {MAX_UPLOAD_BYTES} byte limit"
        )

    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise CsvImportError("file has no header row")

    header = {(name or "").strip().lower() for name in reader.fieldnames}
    missing = REQUIRED_COLUMNS[kind] - header
    if missing:
        raise CsvImportError(
            f"missing required column(s): {', '.join(sorted(missing))}; see data/schemas/README.md"
        )

    rows: list[dict[str, str]] = []
    for row in reader:
        if len(rows) >= MAX_ROWS:
            raise CsvImportError(f"file exceeds the {MAX_ROWS} row limit")
        rows.append({(k or "").strip().lower(): (v or "") for k, v in row.items()})
    return rows


async def import_adp_csv(
    session_factory: async_sessionmaker[AsyncSession],
    text: str,
    *,
    source: str,
    season: int,
    scoring_format: ScoringFormat = ScoringFormat.HALF_PPR,
    league_size: int | None = None,
    snapshot_date: datetime | None = None,
) -> IngestionRunRecorder:
    """Import an ADP file.

    Args:
        session_factory: Async session factory.
        text: Raw CSV text.
        source: Provider name recorded against every value, and shown in the UI.
        season: Season the ADP applies to.
        scoring_format: Format the ADP was collected under.
        league_size: League size behind the average, when known.
        snapshot_date: When this ADP was observed. Defaults to now.
    """
    async with ingestion_run(
        session_factory, provider=source, dataset="adp", requested_resource="csv_upload"
    ) as run:
        rows = read_csv_text(text, ImportKind.ADP)
        run.read(len(rows))
        observed = snapshot_date or utcnow()

        async with session_factory() as session:
            matcher = await PlayerMatcher.build(session)

        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            player_uuid, reason = matcher.match(row)
            if player_uuid is None:
                run.reject(reason, player_name=row.get("player_name"), team=row.get("team"))
                continue
            if player_uuid in seen:
                run.reject("duplicate_player_in_file", player_name=row.get("player_name"))
                continue

            adp, error = _bounded_float(
                row.get("adp"), low=MIN_ADP_EXCLUSIVE, high=MAX_ADP, exclusive_low=True
            )
            if error or adp is None:
                run.reject(
                    f"invalid_adp_{error}",
                    player_name=row.get("player_name"),
                    value=row.get("adp"),
                )
                continue

            seen.add(player_uuid)
            records.append(
                {
                    "player_uuid": player_uuid,
                    "season": season,
                    "scoring_format": scoring_format.value,
                    "league_size": league_size,
                    "adp": adp,
                    "adp_stdev": _optional_float(row.get("adp_stdev"), low=0.0, high=200.0),
                    "min_pick": _optional_float(
                        row.get("min_pick"), low=MIN_ADP_EXCLUSIVE, high=MAX_ADP
                    ),
                    "max_pick": _optional_float(
                        row.get("max_pick"), low=MIN_ADP_EXCLUSIVE, high=MAX_ADP
                    ),
                    "sample_size": _optional_int(row.get("sample_size"), low=0, high=10_000_000),
                    "snapshot_date": observed,
                    "source": source,
                    "source_updated_at": observed,
                    "ingested_at": utcnow(),
                    "observed_at": observed,
                }
            )

        async with session_factory() as session:
            written = await upsert_rows(
                session,
                AdpSnapshot,
                records,
                conflict_columns=[
                    "player_uuid",
                    "season",
                    "scoring_format",
                    "source",
                    "snapshot_date",
                ],
            )
            await session.commit()

        run.wrote(written)
        run.details.update(
            {"season": season, "scoring_format": scoring_format.value, "matched": len(records)}
        )

    return run


async def import_projections_csv(
    session_factory: async_sessionmaker[AsyncSession],
    text: str,
    *,
    source: str,
    season: int,
    scoring_format: ScoringFormat = ScoringFormat.HALF_PPR,
) -> IngestionRunRecorder:
    """Import a projections file.

    Args:
        session_factory: Async session factory.
        text: Raw CSV text.
        source: Provider name recorded against every value.
        season: Season the projections apply to.
        scoring_format: Format the projections were produced under.
    """
    async with ingestion_run(
        session_factory,
        provider=source,
        dataset="projections",
        requested_resource="csv_upload",
    ) as run:
        rows = read_csv_text(text, ImportKind.PROJECTIONS)
        run.read(len(rows))

        async with session_factory() as session:
            matcher = await PlayerMatcher.build(session)

        records: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        now = utcnow()

        for row in rows:
            player_uuid, reason = matcher.match(row)
            if player_uuid is None:
                run.reject(reason, player_name=row.get("player_name"), team=row.get("team"))
                continue

            points, error = _bounded_float(
                row.get("projected_points"),
                low=MIN_PROJECTED_POINTS,
                high=MAX_PROJECTED_POINTS,
            )
            if error or points is None:
                run.reject(
                    f"invalid_projection_{error}",
                    player_name=row.get("player_name"),
                    value=row.get("projected_points"),
                )
                continue

            # 0 marks a season-long projection; see SEASON_LONG_WEEK.
            week = _optional_int(row.get("week"), low=MIN_WEEK, high=MAX_WEEK)
            week = SEASON_LONG_WEEK if week is None else week
            key = (player_uuid, week)
            if key in seen:
                run.reject("duplicate_player_week_in_file", player_name=row.get("player_name"))
                continue
            seen.add(key)

            records.append(
                {
                    "player_uuid": player_uuid,
                    "season": season,
                    "week": week,
                    "scoring_format": scoring_format.value,
                    "projected_points": points,
                    "projected_points_low": _optional_float(
                        row.get("projected_points_low"),
                        low=MIN_PROJECTED_POINTS,
                        high=MAX_PROJECTED_POINTS,
                    ),
                    "projected_points_high": _optional_float(
                        row.get("projected_points_high"),
                        low=MIN_PROJECTED_POINTS,
                        high=MAX_PROJECTED_POINTS,
                    ),
                    "projected_games": _optional_float(
                        row.get("projected_games"), low=0.0, high=23.0
                    ),
                    "source": source,
                    "source_updated_at": now,
                    "ingested_at": now,
                    "observed_at": now,
                }
            )

        async with session_factory() as session:
            written = await upsert_rows(
                session,
                FantasyProjection,
                records,
                conflict_columns=[
                    "player_uuid",
                    "season",
                    "week",
                    "scoring_format",
                    "source",
                ],
            )
            await session.commit()

        run.wrote(written)
        run.details.update(
            {"season": season, "scoring_format": scoring_format.value, "matched": len(records)}
        )

    return run


def summarise_rejections(runs: Iterable[IngestionRunRecorder]) -> dict[str, int]:
    """Aggregate rejection reasons across runs, for a diagnostics view."""
    counts: dict[str, int] = {}
    for run in runs:
        for rejection in run.rejections:
            reason = str(rejection.get("reason", "unknown"))
            counts[reason] = counts.get(reason, 0) + 1
    return counts
