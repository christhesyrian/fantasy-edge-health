"""Point-in-time training set for availability modelling.

The target
----------
For a player in week *w*, the label is whether an official injury report ruled
them out of a game in weeks *w+1 .. w+H*.

That is a narrower claim than "the player was unavailable", and the difference
is stated rather than glossed: it is *a reported ruling-out*, which is what the
public data actually records. A player rested for load management, or inactive
for a coach's decision, is not labelled positive.

Preventing leakage
------------------
Every feature is computed strictly from weeks **< w**. Three specific traps are
avoided by construction:

* **No season aggregates.** A season-total injury count includes the future.
  Everything is a prefix aggregate over earlier weeks only.
* **No current-state tables.** ``current_player_health`` describes *now*, not
  the week being modelled, so it is never read here.
* **The week-w report itself is a feature, the week-w outcome is not.** What was
  known on the report is fair game; whether the player then played is the label
  for the *previous* week, not this one.

The audit in :mod:`fhe.ml.leakage` re-checks these properties against the built
frame rather than trusting this docstring.

Cohort
------
Only player-weeks where the player was demonstrably in the league that week:
they either produced statistics or appeared on an injury report. Without that,
the set fills with retired players whose label is trivially negative.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fhe.core.types import BodyRegion, InjuryDesignation, Position
from fhe.db.models.football import PlayerWeeklyStat, SnapCount
from fhe.db.models.health import InjuryEvent, PracticeReport
from fhe.db.models.player import Player
from fhe.observability import get_logger

log = get_logger(__name__)

# How far ahead the label looks. Four weeks is a fantasy-relevant horizon: long
# enough that the answer matters for a roster decision, short enough that the
# player's situation has not entirely changed.
DEFAULT_HORIZON_WEEKS: Final = 4

# Regular season only. Postseason weeks exist for a minority of teams, and
# including them biases the set toward good teams.
REGULAR_SEASON: Final = "REG"
MIN_WEEK: Final = 1
MAX_REGULAR_WEEK: Final = 18

# Designations that mean the player did not play.
RULED_OUT: Final[frozenset[str]] = frozenset(
    {
        InjuryDesignation.OUT.value,
        InjuryDesignation.IR.value,
        InjuryDesignation.PUP.value,
        InjuryDesignation.NFI.value,
        InjuryDesignation.DID_NOT_REPORT.value,
        InjuryDesignation.NOT_ACTIVE.value,
    }
)

# Body regions that are not injuries at all.
NON_INJURY_REGIONS: Final[frozenset[str]] = frozenset(
    {BodyRegion.REST.value, BodyRegion.NON_INJURY.value}
)

# Soft-tissue regions, which recur at higher rates.
SOFT_TISSUE: Final[frozenset[str]] = frozenset(
    {
        BodyRegion.HAMSTRING.value,
        BodyRegion.QUADRICEPS.value,
        BodyRegion.CALF.value,
        BodyRegion.HIP_GROIN.value,
        BodyRegion.ACHILLES.value,
    }
)

FEATURE_COLUMNS: Final[tuple[str, ...]] = (
    "week",
    "age",
    "years_experience",
    "is_rb",
    "is_wr",
    "is_te",
    "is_qb",
    "prior_reports_this_season",
    "prior_ruled_out_this_season",
    "prior_distinct_regions",
    "prior_soft_tissue_reports",
    "weeks_since_last_report",
    "carried_designation",
    "carried_practice_limited",
    "carried_practice_dnp",
    "rolling_snaps_per_game",
    "rolling_touches_per_game",
    "games_with_stats_so_far",
)

LABEL_COLUMN: Final = "ruled_out_within_horizon"


@dataclass(frozen=True, slots=True)
class DatasetSummary:
    """What was built, and how usable it is."""

    rows: int
    positives: int
    players: int
    seasons: tuple[int, ...]
    horizon_weeks: int
    feature_columns: tuple[str, ...] = field(default=FEATURE_COLUMNS)

    @property
    def positive_rate(self) -> float:
        """Base rate of the label. Any model must beat predicting this."""
        return self.positives / self.rows if self.rows else 0.0

    @property
    def is_trainable(self) -> bool:
        """Whether there is enough signal to attempt a model at all."""
        # Fewer than a few hundred positives, or a degenerate rate, and any
        # metric computed from this is noise dressed as evidence.
        return self.rows >= 2000 and self.positives >= 200 and self.positive_rate < 0.5


def _designation_rank(designation: str) -> int:
    """Ordinal severity, so a designation can be a numeric feature."""
    order = {
        InjuryDesignation.ACTIVE.value: 0,
        InjuryDesignation.UNKNOWN.value: 0,
        InjuryDesignation.QUESTIONABLE.value: 1,
        InjuryDesignation.DOUBTFUL.value: 2,
        InjuryDesignation.OUT.value: 3,
        InjuryDesignation.DID_NOT_REPORT.value: 3,
        InjuryDesignation.NOT_ACTIVE.value: 3,
        InjuryDesignation.PUP.value: 4,
        InjuryDesignation.NFI.value: 4,
        InjuryDesignation.IR.value: 5,
    }
    return order.get(designation, 0)


@dataclass(slots=True)
class _PlayerWeek:
    """One observation before feature assembly."""

    player_uuid: str
    season: int
    week: int
    designation: str | None = None
    region: str | None = None
    practice: str | None = None
    snaps: float | None = None
    touches: float | None = None
    had_stats: bool = False


async def build_training_frame(
    session: AsyncSession,
    *,
    seasons: list[int],
    horizon_weeks: int = DEFAULT_HORIZON_WEEKS,
) -> tuple[list[dict[str, Any]], DatasetSummary]:
    """Assemble the point-in-time training set.

    Args:
        session: Active database session.
        seasons: Seasons to include.
        horizon_weeks: How far ahead the label looks.

    Returns:
        The rows and a summary. Rows carry ``season`` and ``week`` so a caller
        can split temporally; they are *not* features and the audit checks that
        ``season`` never enters the feature set.
    """
    players = {
        row.player_uuid: row
        for row in (
            await session.execute(
                select(Player).where(
                    Player.position.in_(
                        [Position.QB.value, Position.RB.value, Position.WR.value, Position.TE.value]
                    )
                )
            )
        ).scalars()
    }
    if not players:
        return [], DatasetSummary(0, 0, 0, tuple(seasons), horizon_weeks)

    observations = await _collect_observations(session, list(players), seasons)
    rows = _assemble(observations, players, horizon_weeks)

    summary = DatasetSummary(
        rows=len(rows),
        positives=sum(1 for row in rows if row[LABEL_COLUMN] == 1),
        players=len({row["player_uuid"] for row in rows}),
        seasons=tuple(sorted(seasons)),
        horizon_weeks=horizon_weeks,
    )
    log.info(
        "training_frame_built",
        rows=summary.rows,
        positives=summary.positives,
        positive_rate=round(summary.positive_rate, 4),
        players=summary.players,
        trainable=summary.is_trainable,
    )
    return rows, summary


async def _collect_observations(
    session: AsyncSession, uuids: list[str], seasons: list[int]
) -> dict[tuple[str, int], dict[int, _PlayerWeek]]:
    """Gather every player-week signal, keyed by (player, season) then week."""
    grid: dict[tuple[str, int], dict[int, _PlayerWeek]] = defaultdict(dict)

    def cell(player_uuid: str, season: int, week: int) -> _PlayerWeek:
        weeks = grid[(player_uuid, season)]
        if week not in weeks:
            weeks[week] = _PlayerWeek(player_uuid=player_uuid, season=season, week=week)
        return weeks[week]

    injuries = (
        await session.execute(
            select(InjuryEvent).where(
                InjuryEvent.player_uuid.in_(uuids),
                InjuryEvent.season.in_(seasons),
                InjuryEvent.week.between(MIN_WEEK, MAX_REGULAR_WEEK),
            )
        )
    ).scalars()
    for event in injuries:
        entry = cell(event.player_uuid, event.season, event.week)
        entry.designation = event.designation
        entry.region = event.body_region

    practices = (
        await session.execute(
            select(PracticeReport).where(
                PracticeReport.player_uuid.in_(uuids),
                PracticeReport.season.in_(seasons),
                PracticeReport.week.between(MIN_WEEK, MAX_REGULAR_WEEK),
            )
        )
    ).scalars()
    for report in practices:
        cell(report.player_uuid, report.season, report.week).practice = report.status

    stats = (
        await session.execute(
            select(
                PlayerWeeklyStat.player_uuid,
                PlayerWeeklyStat.season,
                PlayerWeeklyStat.week,
                PlayerWeeklyStat.carries,
                PlayerWeeklyStat.targets,
            ).where(
                PlayerWeeklyStat.player_uuid.in_(uuids),
                PlayerWeeklyStat.season.in_(seasons),
                PlayerWeeklyStat.season_type == REGULAR_SEASON,
                PlayerWeeklyStat.week.between(MIN_WEEK, MAX_REGULAR_WEEK),
            )
        )
    ).all()
    for stat_row in stats:
        entry = cell(stat_row.player_uuid, stat_row.season, stat_row.week)
        entry.had_stats = True
        entry.touches = (stat_row.carries or 0.0) + (stat_row.targets or 0.0)

    snaps = (
        await session.execute(
            select(
                SnapCount.player_uuid,
                SnapCount.season,
                SnapCount.week,
                SnapCount.offense_snaps,
            ).where(
                SnapCount.player_uuid.in_(uuids),
                SnapCount.season.in_(seasons),
                SnapCount.week.between(MIN_WEEK, MAX_REGULAR_WEEK),
            )
        )
    ).all()
    for snap_row in snaps:
        cell(snap_row.player_uuid, snap_row.season, snap_row.week).snaps = (
            float(snap_row.offense_snaps) if snap_row.offense_snaps is not None else None
        )

    return grid


def _assemble(
    grid: dict[tuple[str, int], dict[int, _PlayerWeek]],
    players: dict[str, Player],
    horizon_weeks: int,
) -> list[dict[str, Any]]:
    """Walk each player-season forward, emitting one row per eligible week.

    The walk is strictly forward and accumulates only what has already been
    seen, which is what makes the point-in-time property structural rather than
    a promise.
    """
    rows: list[dict[str, Any]] = []

    for (player_uuid, season), weeks in grid.items():
        player = players.get(player_uuid)
        if player is None:
            continue

        position = Position.parse(player.position)
        ruled_out_by_week = {
            week: (entry.designation in RULED_OUT) for week, entry in weeks.items()
        }

        # Running state, containing only weeks already walked.
        prior_reports = 0
        prior_ruled_out = 0
        prior_regions: set[str] = set()
        prior_soft_tissue = 0
        last_report_week: int | None = None
        snap_total = 0.0
        snap_games = 0
        touch_total = 0.0
        stat_games = 0

        for week in range(MIN_WEEK, MAX_REGULAR_WEEK + 1):
            entry = weeks.get(week)

            # Emit before updating state, so every feature describes weeks < w
            # plus this week's *report* — never this week's outcome.
            in_league = entry is not None and (entry.had_stats or entry.designation)
            horizon = range(week + 1, week + 1 + horizon_weeks)
            horizon_observed = any(w in weeks for w in horizon)

            if in_league and horizon_observed:
                label = 1 if any(ruled_out_by_week.get(w, False) for w in horizon) else 0
                rows.append(
                    {
                        "player_uuid": player_uuid,
                        "season": season,
                        "week": week,
                        "age": float(player.age) if player.age is not None else None,
                        "years_experience": (
                            float(player.years_experience)
                            if player.years_experience is not None
                            else None
                        ),
                        "is_rb": 1 if position is Position.RB else 0,
                        "is_wr": 1 if position is Position.WR else 0,
                        "is_te": 1 if position is Position.TE else 0,
                        "is_qb": 1 if position is Position.QB else 0,
                        "prior_reports_this_season": float(prior_reports),
                        "prior_ruled_out_this_season": float(prior_ruled_out),
                        "prior_distinct_regions": float(len(prior_regions)),
                        "prior_soft_tissue_reports": float(prior_soft_tissue),
                        "weeks_since_last_report": (
                            float(week - last_report_week)
                            if last_report_week is not None
                            else float(MAX_REGULAR_WEEK)
                        ),
                        "carried_designation": float(
                            _designation_rank(entry.designation or "") if entry is not None else 0
                        ),
                        "carried_practice_limited": float(
                            1 if entry is not None and entry.practice == "LIMITED" else 0
                        ),
                        "carried_practice_dnp": float(
                            1 if entry is not None and entry.practice == "DNP" else 0
                        ),
                        "rolling_snaps_per_game": (
                            round(snap_total / snap_games, 2) if snap_games else None
                        ),
                        "rolling_touches_per_game": (
                            round(touch_total / stat_games, 2) if stat_games else None
                        ),
                        "games_with_stats_so_far": float(stat_games),
                        LABEL_COLUMN: label,
                    }
                )

            if entry is None:
                continue

            if entry.designation:
                prior_reports += 1
                last_report_week = week
                if entry.designation in RULED_OUT:
                    prior_ruled_out += 1
            if entry.region and entry.region not in NON_INJURY_REGIONS:
                prior_regions.add(entry.region)
                if entry.region in SOFT_TISSUE:
                    prior_soft_tissue += 1
            if entry.snaps is not None:
                snap_total += entry.snaps
                snap_games += 1
            if entry.had_stats:
                stat_games += 1
                touch_total += entry.touches or 0.0

    return rows
