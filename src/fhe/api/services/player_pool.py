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

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fhe.core.depth import DepthChartPlacement
from fhe.core.draft.models import DraftablePlayer
from fhe.core.health import (
    HealthInputs,
    InjuryHistoryEvent,
    WorkloadSummary,
    score_health,
)
from fhe.core.injury import normalize_practice_status
from fhe.core.rookies import MEANINGFUL_ROOKIE_TOUCHES, RookieOpportunity
from fhe.core.schedule import PLAYOFF_WEEKS, PlayoffSchedule
from fhe.core.types import (
    BodyRegion,
    InjuryDesignation,
    Position,
    ScoringFormat,
)
from fhe.core.usage import UsageProfile
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.football import (
    DepthChartSnapshot,
    PlayerWeeklyStat,
    ScheduledGame,
    SnapCount,
)
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

# Positions whose rookie touches describe an offensive role worth measuring.
ROOKIE_TOUCH_POSITIONS: frozenset[Position] = frozenset(
    {Position.QB, Position.RB, Position.WR, Position.TE}
)


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
    with_depth_chart: int = 0
    projection_sources: tuple[str, ...] = field(default=())
    adp_sources: tuple[str, ...] = field(default=())
    projection_observed_at: datetime | None = None
    adp_observed_at: datetime | None = None
    # Set when the league's own scoring family had no rows and a neighbouring
    # one was read instead. None means the values match the league exactly.
    substituted_scoring_format: ScoringFormat | None = None

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
        if self.substituted_scoring_format is not None:
            issues.append(
                "No values are stored for this league's scoring format, so the "
                f"board is ranked on {self.substituted_scoring_format.value.replace('_', '-')} "
                "numbers instead. Receptions are worth a different amount in your "
                "league, which mostly moves pass-catching backs and receivers."
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
    # Anyone we hold a projection or an ADP for is fantasy-relevant by
    # definition and must survive the cut, whatever the provider thinks of
    # their popularity. This is load-bearing rather than tidy: Sleeper gives
    # team defences **no** popularity rank at all, so ordering by that column
    # alone sorted all thirty-two of them past the limit and made a defence
    # undraftable — on a board whose league requires one.
    has_projection = (
        exists()
        .where(FantasyProjection.player_uuid == Player.player_uuid)
        .where(FantasyProjection.season == season)
    )
    has_adp = (
        exists()
        .where(AdpSnapshot.player_uuid == Player.player_uuid)
        .where(AdpSnapshot.season == season)
    )
    market_adp = (
        select(func.min(AdpSnapshot.adp))
        .where(AdpSnapshot.player_uuid == Player.player_uuid, AdpSnapshot.season == season)
        .correlate(Player)
        .scalar_subquery()
    )

    player_rows = (
        (
            await session.execute(
                select(Player)
                .where(
                    Player.is_active.is_(True),
                    Player.position.in_([p.value for p in positions]),
                )
                .order_by(
                    # Fantasy-relevant first...
                    case((or_(has_projection, has_adp), 0), else_=1),
                    # ...then by market draft position, which is a direct
                    # statement of who gets drafted and therefore a better
                    # ranking than popularity wherever it exists. Sorting the
                    # relevant tier by popularity instead still buried the
                    # defences, since they have none: six of thirty-two
                    # survived, in a league that needs twelve.
                    market_adp.is_(None),
                    market_adp,
                    Player.popularity_rank.is_(None),
                    Player.popularity_rank,
                )
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

    projections, projection_swap = await _latest_projections(session, uuids, season, scoring_format)
    adp, adp_swap = await _latest_adp(session, uuids, season, scoring_format)
    health_rows = await _current_health(session, uuids)
    history = await _injury_history(session, uuids, season)
    # Workload describes the season just played, not the one being drafted for.
    workloads = await _workload(session, uuids, season - 1)
    # Same season as workload: the most recent one actually played.
    usage = await _usage(session, uuids, season - 1, scoring_format)
    # The one signal that describes *this* season's role rather than last
    # season's, which is why it is read for `season` and not `season - 1`.
    depth = await _depth_chart(session, uuids, season)
    playoff = await _playoff_schedule(session, season, scoring_format)
    rookie_landing = await _rookie_opportunity(session, season)

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
                usage=usage.get(uuid),
                depth_chart=depth.get(uuid),
                is_rookie=player.rookie_season == season,
                rookie_opportunity=(
                    rookie_landing.get(player.team.upper())
                    if player.team and player.rookie_season == season
                    else None
                ),
                playoff_schedule=(
                    playoff.get(f"{player.team.upper()}:{position.value}") if player.team else None
                ),
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
        with_depth_chart=sum(1 for p in pool if p.depth_chart is not None),
        projection_sources=tuple(sorted({p.source for p in projections.values()})),
        adp_sources=tuple(sorted({a.source for a in adp.values()})),
        projection_observed_at=max(
            (p.observed_at for p in projections.values() if p.observed_at), default=None
        ),
        adp_observed_at=max((a.snapshot_date for a in adp.values()), default=None),
        substituted_scoring_format=projection_swap or adp_swap,
    )

    log.info(
        "player_pool_loaded",
        season=season,
        players=provenance.player_count,
        with_projection=provenance.with_projection,
        with_adp=provenance.with_adp,
        with_health=provenance.with_health,
        with_workload=provenance.with_workload,
        with_depth_chart=provenance.with_depth_chart,
        wanted_scoring_format=scoring_format.value,
        substituted_scoring_format=(
            provenance.substituted_scoring_format.value
            if provenance.substituted_scoring_format
            else None
        ),
    )
    return tuple(pool), provenance


async def _latest_projections(
    session: AsyncSession,
    uuids: list[str],
    season: int,
    scoring_format: ScoringFormat,
) -> tuple[dict[str, FantasyProjection], ScoringFormat | None]:
    """Most recently ingested season-long projection per player.

    Ordered ascending by ingestion time so the dict comprehension keeps the
    newest; several providers can coexist for one player, and the freshest wins.

    Returns the projections and, when the league's own scoring family had none
    stored, the family read instead.
    """
    rows = (
        (
            await session.execute(
                select(FantasyProjection)
                .where(
                    FantasyProjection.player_uuid.in_(uuids),
                    FantasyProjection.season == season,
                    FantasyProjection.week == SEASON_LONG_WEEK,
                )
                .order_by(FantasyProjection.ingested_at)
            )
        )
        .scalars()
        .all()
    )
    chosen, substituted = _resolve_scoring_format(
        {row.scoring_format for row in rows}, scoring_format
    )
    return (
        {row.player_uuid: row for row in rows if row.scoring_format == chosen.value},
        chosen if substituted else None,
    )


async def _latest_adp(
    session: AsyncSession,
    uuids: list[str],
    season: int,
    scoring_format: ScoringFormat,
) -> tuple[dict[str, AdpSnapshot], ScoringFormat | None]:
    """Most recent ADP snapshot per player.

    ADP is a time series precisely because it moves daily, so the newest
    snapshot is the only one worth ranking against.

    Returns the snapshots and, when the league's own scoring family had none
    stored, the family read instead.
    """
    rows = (
        (
            await session.execute(
                select(AdpSnapshot)
                .where(
                    AdpSnapshot.player_uuid.in_(uuids),
                    AdpSnapshot.season == season,
                )
                .order_by(AdpSnapshot.snapshot_date)
            )
        )
        .scalars()
        .all()
    )
    chosen, substituted = _resolve_scoring_format(
        {row.scoring_format for row in rows}, scoring_format
    )
    return (
        {row.player_uuid: row for row in rows if row.scoring_format == chosen.value},
        chosen if substituted else None,
    )


# Scoring families to fall back on, best first, when a league's own family has
# no stored values. Half-PPR sits between the other two, so either neighbour is
# one step away and the more widely published one is preferred; PPR and standard
# both step to half-PPR first because it is the nearer of the two.
#
# This exists because a Sleeper league scoring 0.5 per reception - Sleeper's own
# default, and so the shape most leagues take - found no rows at all: every
# FantasyPros import is labelled "ppr", since the free tier ignores the scoring
# parameter entirely. The board came up with no projections and no ADP.
_SCORING_FALLBACKS: dict[ScoringFormat, tuple[ScoringFormat, ...]] = {
    ScoringFormat.HALF_PPR: (ScoringFormat.PPR, ScoringFormat.STANDARD),
    ScoringFormat.PPR: (ScoringFormat.HALF_PPR, ScoringFormat.STANDARD),
    ScoringFormat.STANDARD: (ScoringFormat.HALF_PPR, ScoringFormat.PPR),
}


def _resolve_scoring_format(
    available: set[str], wanted: ScoringFormat
) -> tuple[ScoringFormat, bool]:
    """Pick which stored scoring family to read, and say whether it was a swap.

    The whole pool reads one family. Filling the gaps player by player would
    rank some players on PPR points and others on standard, which is not a
    board at all - the ordering it produced would correspond to no league that
    exists.
    """
    if wanted.value in available:
        return wanted, False
    for candidate in _SCORING_FALLBACKS[wanted]:
        if candidate.value in available:
            return candidate, True
    return wanted, False


# Which weekly fantasy-points column each scoring family reads.
_POINTS_COLUMN = {
    ScoringFormat.PPR: PlayerWeeklyStat.fantasy_points_ppr,
    ScoringFormat.HALF_PPR: PlayerWeeklyStat.fantasy_points_half_ppr,
    ScoringFormat.STANDARD: PlayerWeeklyStat.fantasy_points_standard,
}


async def _rookie_opportunity(session: AsyncSession, season: int) -> dict[str, RookieOpportunity]:
    """How willing each team has been to play rookies, under its current coach.

    Tenure is the load-bearing part. Coaching staffs differ persistently in
    whether they play rookies, and that travels with the staff rather than the
    franchise, so a season under a previous coach says nothing about the
    current one. Only the *contiguous* run ending at the upcoming season counts
    — a coach who had an earlier interim spell at the same club was working in
    a different situation, and splicing the two would read as continuity that
    never existed.
    """
    game_rows = (
        await session.execute(
            select(
                ScheduledGame.season,
                ScheduledGame.home_team,
                ScheduledGame.home_coach,
                ScheduledGame.away_team,
                ScheduledGame.away_coach,
            ).where(ScheduledGame.game_type == "REG")
        )
    ).all()
    if not game_rows:
        return {}

    # The coach who took most of a team's games in a season is that season's
    # coach; a mid-season replacement should not make the year ambiguous.
    games_by: dict[tuple[int, str, str], int] = defaultdict(int)
    for row in game_rows:
        if row.home_coach:
            games_by[(int(row.season), str(row.home_team).upper(), str(row.home_coach))] += 1
        if row.away_coach:
            games_by[(int(row.season), str(row.away_team).upper(), str(row.away_coach))] += 1

    coach_of: dict[tuple[int, str], str] = {}
    best: dict[tuple[int, str], int] = {}
    for (year, team, coach), count in games_by.items():
        if count > best.get((year, team), 0):
            best[(year, team)] = count
            coach_of[(year, team)] = coach

    teams = {team for (_, team) in coach_of}
    tenure: dict[str, tuple[str, list[int]]] = {}
    for team in teams:
        current = coach_of.get((season, team))
        if current is None:
            continue
        years: list[int] = []
        year = season - 1
        while coach_of.get((year, team)) == current:
            years.append(year)
            year -= 1
        tenure[team] = (current, sorted(years))

    # Offensive touches taken by rookies, per team and season.
    touch_rows = (
        await session.execute(
            select(
                PlayerWeeklyStat.season,
                PlayerWeeklyStat.team,
                PlayerWeeklyStat.player_uuid,
                func.sum(PlayerWeeklyStat.carries + PlayerWeeklyStat.receptions).label("touches"),
            )
            .join(Player, Player.player_uuid == PlayerWeeklyStat.player_uuid)
            .where(
                PlayerWeeklyStat.season_type == REGULAR_SEASON,
                Player.rookie_season.isnot(None),
                Player.rookie_season == PlayerWeeklyStat.season,
                Player.position.in_([p.value for p in ROOKIE_TOUCH_POSITIONS]),
            )
            .group_by(PlayerWeeklyStat.season, PlayerWeeklyStat.team, PlayerWeeklyStat.player_uuid)
        )
    ).all()

    team_season_touches: dict[tuple[int, str], float] = defaultdict(float)
    workhorse: set[tuple[int, str]] = set()
    for touch_row in touch_rows:
        if touch_row.touches is None or not touch_row.team:
            continue
        key = (int(touch_row.season), str(touch_row.team).upper())
        team_season_touches[key] += float(touch_row.touches)
        if float(touch_row.touches) >= MEANINGFUL_ROOKIE_TOUCHES:
            workhorse.add(key)

    averages: dict[str, float] = {}
    for team, (_, years) in tenure.items():
        if not years:
            continue
        averages[team] = sum(team_season_touches.get((y, team), 0.0) for y in years) / len(years)

    order = sorted(averages, key=lambda t: -averages[t])
    rank_of = {team: index + 1 for index, team in enumerate(order)}

    opportunities: dict[str, RookieOpportunity] = {}
    for team, (coach, years) in tenure.items():
        latest = max(years) if years else None
        opportunities[team] = RookieOpportunity(
            team=team,
            coach=coach,
            seasons_under_coach=len(years),
            average_rookie_touches=(round(averages[team], 1) if team in averages else None),
            rank=rank_of.get(team),
            teams_ranked=len(order),
            had_recent_workhorse=latest is not None and (latest, team) in workhorse,
        )
    return opportunities


async def _playoff_schedule(
    session: AsyncSession, season: int, scoring_format: ScoringFormat
) -> dict[str, PlayoffSchedule]:
    """Playoff-week matchup difficulty per team and position.

    Two halves, both from data this system already holds. Defensive strength
    comes from its *own* weekly stats — every stat line records the opponent it
    was produced against, so "points allowed to running backs" is a group-by,
    not a new provider. The fixtures come from the ingested schedule.

    Keyed by ``"TEAM:POS"`` because difficulty is a property of the pairing: the
    same defence can be brutal against the run and generous to receivers.
    """
    points = _POINTS_COLUMN[scoring_format]

    # What each defence allowed to each position, per game, last season.
    allowed_rows = (
        await session.execute(
            select(
                PlayerWeeklyStat.opponent.label("defence"),
                Player.position.label("position"),
                func.avg(points).label("allowed"),
                func.count().label("games"),
            )
            .join(Player, Player.player_uuid == PlayerWeeklyStat.player_uuid)
            .where(
                PlayerWeeklyStat.season == season - 1,
                PlayerWeeklyStat.season_type == REGULAR_SEASON,
                PlayerWeeklyStat.opponent.isnot(None),
            )
            .group_by(PlayerWeeklyStat.opponent, Player.position)
        )
    ).all()
    if not allowed_rows:
        return {}

    allowed: dict[tuple[str, str], float] = {
        (str(row.defence).upper(), str(row.position)): float(row.allowed)
        for row in allowed_rows
        if row.allowed is not None
    }
    by_position: dict[str, list[float]] = defaultdict(list)
    for (_, position), value in allowed.items():
        by_position[position].append(value)
    league_average: dict[str, float] = {
        position: sum(values) / len(values) for position, values in by_position.items() if values
    }

    # Who each team plays in the fantasy playoff weeks.
    fixtures = (
        await session.execute(
            select(
                ScheduledGame.week,
                ScheduledGame.home_team,
                ScheduledGame.away_team,
            ).where(
                ScheduledGame.season == season,
                ScheduledGame.game_type == "REG",
                ScheduledGame.week.in_(PLAYOFF_WEEKS),
            )
        )
    ).all()

    opponents: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for row in fixtures:
        opponents[str(row.home_team).upper()].append((int(row.week), str(row.away_team).upper()))
        opponents[str(row.away_team).upper()].append((int(row.week), str(row.home_team).upper()))

    schedules: dict[str, PlayoffSchedule] = {}
    for team, weeks in opponents.items():
        ordered = sorted(weeks)
        for position, average in league_average.items():
            faced = [
                allowed[(opponent, position)]
                for _, opponent in ordered
                if (opponent, position) in allowed
            ]
            if not faced:
                continue
            schedules[f"{team}:{position}"] = PlayoffSchedule(
                weeks_covered=len(faced),
                opponents=tuple(opponent for _, opponent in ordered),
                points_allowed_per_game=round(sum(faced) / len(faced), 2),
                league_average=round(average, 2),
            )
    return schedules


def _stdev(mean: float | None, mean_of_squares: float | None) -> float | None:
    """Population standard deviation from a mean and a mean of squares.

    Clamped at zero before the square root: floating-point error can make a
    constant series produce a variance of -1e-12.
    """
    if mean is None or mean_of_squares is None:
        return None
    variance = float(mean_of_squares) - float(mean) ** 2
    return round(math.sqrt(max(0.0, variance)), 2)


async def _usage(
    session: AsyncSession, uuids: list[str], season: int, scoring_format: ScoringFormat
) -> dict[str, UsageProfile]:
    """Measured opportunity and scoring volatility for a season.

    Separate from :func:`_workload`, which serves the health model's exposure
    terms. This one answers a different question — whether a projection is
    corroborated by opportunity, and how steady the scoring was — so it reads
    snap *share* rather than snap count, and the spread of points rather than
    their level.

    The spread is derived from two averages rather than a `stddev` function,
    because `stddev_samp` is PostgreSQL-only and this application also runs on
    SQLite — the zero-infrastructure fallback would have raised "no such
    function" the moment a board was built. Mean and mean-of-squares are
    portable, and the population standard deviation they give differs
    negligibly from the sample one across seventeen games.

    Still aggregated in SQL: pulling eighteen weeks for six hundred players to
    average them in Python would move ten thousand rows to do arithmetic the
    database already knows how to do.
    """
    points = _POINTS_COLUMN[scoring_format]
    stat_rows = (
        await session.execute(
            select(
                PlayerWeeklyStat.player_uuid,
                func.count().label("games"),
                func.avg(PlayerWeeklyStat.carries + PlayerWeeklyStat.targets).label("touches"),
                func.avg(points).label("points"),
                func.avg(points * points).label("points_squared"),
            )
            .where(
                PlayerWeeklyStat.player_uuid.in_(uuids),
                PlayerWeeklyStat.season == season,
                PlayerWeeklyStat.season_type == REGULAR_SEASON,
            )
            .group_by(PlayerWeeklyStat.player_uuid)
        )
    ).all()

    share_rows = (
        await session.execute(
            select(
                SnapCount.player_uuid,
                func.avg(SnapCount.offense_snap_pct).label("share"),
            )
            .where(
                SnapCount.player_uuid.in_(uuids),
                SnapCount.season == season,
                SnapCount.offense_snap_pct.isnot(None),
            )
            .group_by(SnapCount.player_uuid)
        )
    ).all()
    share_by_uuid = {row.player_uuid: row.share for row in share_rows}

    def _share(uuid: str) -> float | None:
        raw = share_by_uuid.get(uuid)
        if raw is None:
            return None
        # nflverse publishes the share as a fraction already; guard against a
        # percentage should that ever change, rather than silently reading 85%
        # of the snaps as 8500%.
        value = float(raw)
        return round(value / 100 if value > 1.5 else value, 3)

    profiles: dict[str, UsageProfile] = {}
    for row in stat_rows:
        profiles[row.player_uuid] = UsageProfile(
            season=season,
            games_sampled=int(row.games) if row.games is not None else None,
            snap_share=_share(row.player_uuid),
            touches_per_game=round(float(row.touches), 2) if row.touches is not None else None,
            points_per_game=round(float(row.points), 2) if row.points is not None else None,
            points_stdev=_stdev(row.points, row.points_squared),
        )
    return profiles


async def _depth_chart(
    session: AsyncSession, uuids: list[str], season: int
) -> dict[str, DepthChartPlacement]:
    """The most recent depth-chart listing per player.

    Stored at the season-long sentinel because the provider publishes a scrape
    timestamp rather than a week, so there is exactly one row per player per
    source and the newest source wins on ties.
    """
    rows = (
        (
            await session.execute(
                select(DepthChartSnapshot)
                .where(
                    DepthChartSnapshot.player_uuid.in_(uuids),
                    DepthChartSnapshot.season == season,
                    DepthChartSnapshot.week == SEASON_LONG_WEEK,
                    DepthChartSnapshot.depth_order.isnot(None),
                )
                .order_by(DepthChartSnapshot.observed_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        row.player_uuid: DepthChartPlacement(
            team=(row.team or "").upper(),
            position=row.depth_position or "",
            # Guarded by the isnot(None) filter above; the cast keeps mypy
            # honest about a column that is nullable in the schema.
            rank=int(row.depth_order or 0),
            observed_at=row.observed_at,
        )
        for row in rows
    }


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
