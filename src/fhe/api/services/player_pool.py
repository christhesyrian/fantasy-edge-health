"""Build the engine's player pool from curated database rows.

This is the step the architecture diagram calls "curated entities → draft
intelligence". It reads players, their latest projection and ADP, their current
health, and their recent injury history, then hands the draft engine exactly the
same :class:`DraftablePlayer` shape the synthetic demo pool produces.

That symmetry is the point: the engine cannot tell whether it is reasoning about
real data or the demo, so a live draft and a rehearsal exercise identical code.

Query strategy
--------------
Six bounded queries, assembled in Python, rather than one join. A single join
across projections, ADP snapshots, health, injury history and weekly stats would
fan out multiplicatively and return a row per injury event per week per player.
The pool is a few hundred players, so assembling in memory is both faster and far
easier to read.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fhe.core.draft.models import DraftablePlayer
from fhe.core.health import (
    HealthInputs,
    InjuryHistoryEvent,
    WorkloadSummary,
    score_health,
)
from fhe.core.injury import normalize_practice_status
from fhe.core.types import (
    BodyRegion,
    InjuryDesignation,
    Position,
    ScoringFormat,
)
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.football import PlayerWeeklyStat, SnapCount
from fhe.db.models.health import CurrentPlayerHealth, InjuryEvent
from fhe.db.models.player import Player
from fhe.observability import get_logger

log = get_logger(__name__)

# Positions the draft engine reasons about.
POOL_POSITIONS: frozenset[Position] = frozenset(
    {Position.QB, Position.RB, Position.WR, Position.TE, Position.K, Position.DEF}
)

# How deep the pool goes. A 12-team, 15-round draft consumes 180 players; this
# leaves ample room for the board below the last pick without loading every
# practice-squad body ever recorded.
DEFAULT_POOL_LIMIT = 600

# Seasons of injury history the health model considers.
HISTORY_SEASONS = 3

# Only regular-season games count toward workload. Including the postseason
# would inflate per-game usage for the minority of players whose team went deep,
# which is a property of their team rather than their exposure.
REGULAR_SEASON = "REG"


@dataclass(frozen=True, slots=True)
class PoolProvenance:
    """Where a pool's numbers came from, and how complete it is.

    Surfaced to the UI so a live board can say, truthfully, that it is running
    without projections rather than quietly ranking on ADP alone.
    """

    player_count: int
    with_projection: int
    with_adp: int
    with_health: int
    with_workload: int = 0
    projection_sources: tuple[str, ...] = field(default=())
    adp_sources: tuple[str, ...] = field(default=())
    projection_observed_at: datetime | None = None
    adp_observed_at: datetime | None = None

    @property
    def has_projections(self) -> bool:
        """Whether any player carries a projection."""
        return self.with_projection > 0

    @property
    def warnings(self) -> tuple[str, ...]:
        """Plain-language gaps a user should know about before trusting the board."""
        issues: list[str] = []
        if self.player_count == 0:
            issues.append("No players in the database. Run `fhe ingest players` first.")
            return tuple(issues)
        if not self.with_projection:
            issues.append(
                "No projections imported. Value over replacement is unavailable, "
                "so ranking falls back to market ADP. Import a projections CSV to "
                "enable the full engine."
            )
        if not self.with_adp:
            issues.append(
                "No ADP imported. Next-pick survival probability and ADP value cannot be computed."
            )
        if self.with_health == 0:
            # Coverage is *expected* to be low: a health row is only written
            # when a provider actually reports something, so a healthy player
            # correctly has none. Warning on a coverage ratio would fire on
            # every normal database. Zero rows, though, means ingestion never
            # ran, and every availability score will be uninformed.
            issues.append(
                "No health data ingested. Availability risk will be "
                "low-confidence for every player. Run `fhe ingest players`."
            )
        return tuple(issues)


def _designation(raw: str | None) -> InjuryDesignation:
    """Parse a stored designation, which is already normalised."""
    if not raw:
        return InjuryDesignation.ACTIVE
    try:
        return InjuryDesignation(raw)
    except ValueError:
        return InjuryDesignation.UNKNOWN


def _region(raw: str | None) -> BodyRegion | None:
    """Parse a stored body region, which is already normalised."""
    if not raw:
        return None
    try:
        return BodyRegion(raw)
    except ValueError:
        return None


async def load_player_pool(
    session: AsyncSession,
    *,
    season: int,
    scoring_format: ScoringFormat,
    as_of: date,
    positions: frozenset[Position] = POOL_POSITIONS,
    limit: int = DEFAULT_POOL_LIMIT,
) -> tuple[tuple[DraftablePlayer, ...], PoolProvenance]:
    """Assemble the draft pool for a league configuration.

    Args:
        session: Active database session.
        season: Season the projections and ADP apply to.
        scoring_format: Reception-scoring family to select values for.
        as_of: Date the health assessment is made for.
        positions: Positions to include.
        limit: Maximum players, ordered by market popularity.

    Returns:
        The pool, and a provenance record describing how complete it is.
    """
    player_rows = (
        (
            await session.execute(
                select(Player)
                .where(
                    Player.is_active.is_(True),
                    Player.position.in_([p.value for p in positions]),
                )
                # Sleeper's popularity ordering is the only ranking available before
                # projections exist, so it decides who makes the cut.
                .order_by(Player.popularity_rank.is_(None), Player.popularity_rank)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    if not player_rows:
        log.warning("player_pool_empty", season=season)
        return (), PoolProvenance(0, 0, 0, 0)

    uuids = [player.player_uuid for player in player_rows]

    projections = await _latest_projections(session, uuids, season, scoring_format)
    adp = await _latest_adp(session, uuids, season, scoring_format)
    health_rows = await _current_health(session, uuids)
    history = await _injury_history(session, uuids, season)
    # Workload describes the season just played, not the one being drafted for.
    workloads = await _workload(session, uuids, season - 1)

    pool: list[DraftablePlayer] = []
    for player in player_rows:
        uuid = player.player_uuid
        position = Position.parse(player.position)
        projection = projections.get(uuid)
        adp_row = adp.get(uuid)
        health_row = health_rows.get(uuid)
        events = history.get(uuid, ())

        practice = (
            (normalize_practice_status(health_row.practice_status),)
            if health_row and health_row.practice_status
            else ()
        )

        assessment = score_health(
            HealthInputs(
                player_uuid=uuid,
                position=position,
                as_of=as_of,
                current_season=season,
                designation=(
                    _designation(health_row.designation) if health_row else InjuryDesignation.ACTIVE
                ),
                current_injury_region=_region(health_row.body_region) if health_row else None,
                injury_start_date=health_row.injury_start_date if health_row else None,
                practice_statuses=practice,
                injury_history=events,
                age=player.age,
                years_experience=player.years_experience,
                workload=workloads.get(uuid),
            )
        )

        pool.append(
            DraftablePlayer(
                player_uuid=uuid,
                name=player.full_name,
                position=position,
                team=player.team,
                projected_points=projection.projected_points if projection else None,
                projection_source=projection.source if projection else None,
                adp=adp_row.adp if adp_row else None,
                adp_stdev=adp_row.adp_stdev if adp_row else None,
                adp_source=adp_row.source if adp_row else None,
                health=assessment,
                injury_history=events,
                workload=workloads.get(uuid),
                bye_week=None,
                age=player.age,
                years_experience=player.years_experience,
                popularity_rank=player.popularity_rank,
            )
        )

    provenance = PoolProvenance(
        player_count=len(pool),
        with_projection=sum(1 for p in pool if p.projected_points is not None),
        with_adp=sum(1 for p in pool if p.adp is not None),
        with_health=len(health_rows),
        with_workload=len(workloads),
        projection_sources=tuple(sorted({p.source for p in projections.values()})),
        adp_sources=tuple(sorted({a.source for a in adp.values()})),
        projection_observed_at=max(
            (p.observed_at for p in projections.values() if p.observed_at), default=None
        ),
        adp_observed_at=max((a.snapshot_date for a in adp.values()), default=None),
    )

    log.info(
        "player_pool_loaded",
        season=season,
        players=provenance.player_count,
        with_projection=provenance.with_projection,
        with_adp=provenance.with_adp,
        with_health=provenance.with_health,
        with_workload=provenance.with_workload,
    )
    return tuple(pool), provenance


async def _latest_projections(
    session: AsyncSession,
    uuids: list[str],
    season: int,
    scoring_format: ScoringFormat,
) -> dict[str, FantasyProjection]:
    """Most recently ingested season-long projection per player.

    Ordered ascending by ingestion time so the dict comprehension keeps the
    newest; several providers can coexist for one player, and the freshest wins.
    """
    rows = (
        (
            await session.execute(
                select(FantasyProjection)
                .where(
                    FantasyProjection.player_uuid.in_(uuids),
                    FantasyProjection.season == season,
                    FantasyProjection.week == SEASON_LONG_WEEK,
                    FantasyProjection.scoring_format == scoring_format.value,
                )
                .order_by(FantasyProjection.ingested_at)
            )
        )
        .scalars()
        .all()
    )
    return {row.player_uuid: row for row in rows}


async def _latest_adp(
    session: AsyncSession,
    uuids: list[str],
    season: int,
    scoring_format: ScoringFormat,
) -> dict[str, AdpSnapshot]:
    """Most recent ADP snapshot per player.

    ADP is a time series precisely because it moves daily, so the newest
    snapshot is the only one worth ranking against.
    """
    rows = (
        (
            await session.execute(
                select(AdpSnapshot)
                .where(
                    AdpSnapshot.player_uuid.in_(uuids),
                    AdpSnapshot.season == season,
                    AdpSnapshot.scoring_format == scoring_format.value,
                )
                .order_by(AdpSnapshot.snapshot_date)
            )
        )
        .scalars()
        .all()
    )
    return {row.player_uuid: row for row in rows}


async def _workload(
    session: AsyncSession, uuids: list[str], season: int
) -> dict[str, WorkloadSummary]:
    """Per-game usage for a season, from weekly stats and snap counts.

    Aggregated in SQL rather than fetched per week: a 600-player pool across 18
    weeks is ten thousand rows the application does not need to see.

    Snap counts and production are averaged over their *own* game counts, since
    the two datasets join on different identifiers and a player can appear in
    one without the other.
    """
    stat_rows = (
        await session.execute(
            select(
                PlayerWeeklyStat.player_uuid,
                func.count().label("games"),
                func.avg(PlayerWeeklyStat.carries).label("carries"),
                func.avg(PlayerWeeklyStat.targets).label("targets"),
            )
            .where(
                PlayerWeeklyStat.player_uuid.in_(uuids),
                PlayerWeeklyStat.season == season,
                PlayerWeeklyStat.season_type == REGULAR_SEASON,
            )
            .group_by(PlayerWeeklyStat.player_uuid)
        )
    ).all()

    snap_rows = (
        await session.execute(
            select(
                SnapCount.player_uuid,
                func.avg(SnapCount.offense_snaps).label("snaps"),
            )
            .where(
                SnapCount.player_uuid.in_(uuids),
                SnapCount.season == season,
                SnapCount.offense_snaps.isnot(None),
            )
            .group_by(SnapCount.player_uuid)
        )
    ).all()

    snaps_by_uuid = {row.player_uuid: row.snaps for row in snap_rows}

    summaries: dict[str, WorkloadSummary] = {}
    for row in stat_rows:
        summaries[row.player_uuid] = WorkloadSummary(
            season=season,
            games_played=int(row.games) if row.games is not None else None,
            snaps_per_game=(
                round(float(snaps_by_uuid[row.player_uuid]), 1)
                if snaps_by_uuid.get(row.player_uuid) is not None
                else None
            ),
            carries_per_game=round(float(row.carries), 1) if row.carries is not None else None,
            targets_per_game=round(float(row.targets), 1) if row.targets is not None else None,
        )

    # A player with snaps but no production line still has measured exposure.
    for uuid, snaps in snaps_by_uuid.items():
        if uuid not in summaries and snaps is not None:
            summaries[uuid] = WorkloadSummary(season=season, snaps_per_game=round(float(snaps), 1))

    return summaries


async def _current_health(
    session: AsyncSession, uuids: list[str]
) -> dict[str, CurrentPlayerHealth]:
    """Latest known health status per player."""
    rows = (
        (
            await session.execute(
                select(CurrentPlayerHealth).where(CurrentPlayerHealth.player_uuid.in_(uuids))
            )
        )
        .scalars()
        .all()
    )
    return {row.player_uuid: row for row in rows}


async def _injury_history(
    session: AsyncSession, uuids: list[str], season: int
) -> dict[str, tuple[InjuryHistoryEvent, ...]]:
    """Recent injury events per player, newest season first."""
    rows = (
        (
            await session.execute(
                select(InjuryEvent)
                .where(
                    InjuryEvent.player_uuid.in_(uuids),
                    InjuryEvent.season > season - HISTORY_SEASONS - 1,
                    InjuryEvent.season <= season,
                )
                .order_by(InjuryEvent.season.desc(), InjuryEvent.week.desc())
            )
        )
        .scalars()
        .all()
    )

    grouped: dict[str, list[InjuryHistoryEvent]] = defaultdict(list)
    for row in rows:
        region = _region(row.body_region)
        if region is None:
            continue
        grouped[row.player_uuid].append(
            InjuryHistoryEvent(
                season=row.season,
                week=row.week if row.week != SEASON_LONG_WEEK else None,
                region=region,
                raw_descriptor=row.raw_primary_injury or region.value,
                designation=_designation(row.designation),
                games_missed=row.games_missed,
            )
        )
    return {uuid: tuple(events) for uuid, events in grouped.items()}


__all__ = [
    "DEFAULT_POOL_LIMIT",
    "POOL_POSITIONS",
    "PoolProvenance",
    "load_player_pool",
]
