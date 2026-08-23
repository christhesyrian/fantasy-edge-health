"""Automated data-quality checks.

Answers "is the data behind this board trustworthy?" as a query rather than a
hope. Every check writes a row to ``data_quality_results``, which the
diagnostics endpoint surfaces.

Design
------
* **A check is a pure predicate over a count.** It runs one bounded query and
  compares the result to a threshold. Nothing here mutates data, so a check can
  never make things worse.
* **Severity is separate from failure.** An ERROR means the data is wrong and
  something downstream will be misleading. A WARNING means it is thinner than
  expected, which is often legitimate — a preseason database genuinely has few
  injury reports.
* **Failures are recorded, not raised.** A quality run reports on the whole
  dataset; aborting at the first problem would hide the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Any, Final

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.core.types import Position
from fhe.db.base import utcnow
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.football import PlayerWeeklyStat, SnapCount
from fhe.db.models.health import InjuryEvent
from fhe.db.models.pipeline import DataQualityResult
from fhe.db.models.player import Player, PlayerExternalId, PlayerIdentityConflict
from fhe.observability import get_logger

log = get_logger(__name__)

# Rows retained as examples of a failure. Enough to diagnose, small enough that
# a catastrophic check does not write a megabyte of JSON.
MAX_SAMPLES: Final = 10

# Identity resolution below this is a broken crosswalk, not a quiet week.
MIN_GSIS_COVERAGE: Final = 0.40
# Below this the pool is too thin to draft from.
MIN_ACTIVE_PLAYERS: Final = 300

VALID_POSITIONS: Final[frozenset[str]] = frozenset(
    p.value for p in Position if p is not Position.UNKNOWN
)
MIN_PLAUSIBLE_AGE: Final = 17.0
MAX_PLAUSIBLE_AGE: Final = 50.0
MIN_WEEK: Final = 0  # SEASON_LONG_WEEK sentinel
MAX_WEEK: Final = 23


@unique
class Severity(StrEnum):
    """How much a failed check matters."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check's outcome."""

    name: str
    dataset: str
    severity: Severity
    passed: bool
    observed: float
    threshold: float | None
    message: str
    samples: tuple[dict[str, Any], ...] = ()

    @property
    def is_blocking(self) -> bool:
        """Whether this failure means the data should not be trusted."""
        return not self.passed and self.severity is Severity.ERROR


async def _count(session: AsyncSession, statement: Select[Any]) -> int:
    """Run a count query."""
    return (await session.execute(statement)).scalar() or 0


async def _samples(
    session: AsyncSession, statement: Select[Any], limit: int = MAX_SAMPLES
) -> tuple[dict[str, Any], ...]:
    """Fetch a few offending rows as evidence."""
    rows = (await session.execute(statement.limit(limit))).all()
    return tuple(dict(row._mapping) for row in rows)


async def check_player_pool_depth(session: AsyncSession) -> CheckResult:
    """The player table must be deep enough to draft from."""
    total = await _count(
        session, select(func.count()).select_from(Player).where(Player.is_active.is_(True))
    )
    return CheckResult(
        name="player_pool_depth",
        dataset="players",
        severity=Severity.ERROR,
        passed=total >= MIN_ACTIVE_PLAYERS,
        observed=total,
        threshold=MIN_ACTIVE_PLAYERS,
        message=(
            f"{total} active players"
            if total >= MIN_ACTIVE_PLAYERS
            else f"only {total} active players; run `fhe ingest players`"
        ),
    )


async def check_positions_are_known(session: AsyncSession) -> CheckResult:
    """No player should carry an unrecognised position.

    An UNKNOWN position silently removes a player from every positional
    calculation - replacement level, scarcity, roster need - without any error.
    """
    bad = await _count(
        session,
        select(func.count())
        .select_from(Player)
        .where(Player.position.notin_(list(VALID_POSITIONS))),
    )
    samples = await _samples(
        session,
        select(Player.player_uuid, Player.full_name, Player.position).where(
            Player.position.notin_(list(VALID_POSITIONS))
        ),
    )
    return CheckResult(
        name="positions_are_known",
        dataset="players",
        severity=Severity.ERROR,
        passed=bad == 0,
        observed=bad,
        threshold=0,
        message=f"{bad} players with an unrecognised position",
        samples=samples,
    )


async def check_ages_are_plausible(session: AsyncSession) -> CheckResult:
    """Ages outside a human range mean a parsing error upstream."""
    condition = Player.age.isnot(None) & (
        (Player.age < MIN_PLAUSIBLE_AGE) | (Player.age > MAX_PLAUSIBLE_AGE)
    )
    bad = await _count(session, select(func.count()).select_from(Player).where(condition))
    samples = await _samples(
        session, select(Player.player_uuid, Player.full_name, Player.age).where(condition)
    )
    return CheckResult(
        name="ages_are_plausible",
        dataset="players",
        severity=Severity.ERROR,
        passed=bad == 0,
        observed=bad,
        threshold=0,
        message=f"{bad} players with an implausible age",
        samples=samples,
    )


async def check_identity_coverage(session: AsyncSession) -> CheckResult:
    """Enough players must link to nflverse for history to be usable.

    A collapse here is silent: the board still renders, but availability risk
    quietly loses its evidence base.
    """
    players = await _count(
        session, select(func.count()).select_from(Player).where(Player.is_active.is_(True))
    )
    # Joined to active players on purpose. Counting gsis rows across the whole
    # table and dividing by the active subset produced 102% coverage, which is
    # the kind of impossible number that discredits every other check.
    linked = await _count(
        session,
        select(func.count())
        .select_from(PlayerExternalId)
        .join(Player, Player.player_uuid == PlayerExternalId.player_uuid)
        .where(PlayerExternalId.system == "gsis_id", Player.is_active.is_(True)),
    )
    coverage = linked / players if players else 0.0
    return CheckResult(
        name="identity_coverage",
        dataset="player_external_ids",
        severity=Severity.WARNING,
        passed=coverage >= MIN_GSIS_COVERAGE,
        observed=round(coverage, 4),
        threshold=MIN_GSIS_COVERAGE,
        message=(
            f"{coverage:.1%} of active players link to nflverse via gsis_id"
            + ("" if coverage >= MIN_GSIS_COVERAGE else "; is the crosswalk reachable?")
        ),
    )


async def check_unresolved_conflicts(session: AsyncSession) -> CheckResult:
    """Unresolved identity conflicts are known unknowns worth watching."""
    unresolved = await _count(
        session,
        select(func.count())
        .select_from(PlayerIdentityConflict)
        .where(PlayerIdentityConflict.resolved.is_(False)),
    )
    samples = await _samples(
        session,
        select(
            PlayerIdentityConflict.observed_name,
            PlayerIdentityConflict.reason,
            PlayerIdentityConflict.detail,
        ).where(PlayerIdentityConflict.resolved.is_(False)),
    )
    return CheckResult(
        name="unresolved_identity_conflicts",
        dataset="player_identity_conflicts",
        severity=Severity.INFO,
        passed=True,  # informational: a conflict is recorded, not a failure
        observed=unresolved,
        threshold=None,
        message=f"{unresolved} players await identity adjudication",
        samples=samples,
    )


async def check_no_duplicate_weekly_stats(session: AsyncSession) -> CheckResult:
    """A player must not have two production rows for one week from one source.

    The unique constraint enforces this, so a failure means the constraint was
    dropped or the data was written around it.
    """
    duplicates = (
        select(
            PlayerWeeklyStat.player_uuid,
            PlayerWeeklyStat.season,
            PlayerWeeklyStat.week,
            func.count().label("rows"),
        )
        .group_by(PlayerWeeklyStat.player_uuid, PlayerWeeklyStat.season, PlayerWeeklyStat.week)
        .having(func.count() > 1)
    )

    rows = (await session.execute(duplicates)).all()
    return CheckResult(
        name="no_duplicate_weekly_stats",
        dataset="player_weekly_stats",
        severity=Severity.ERROR,
        passed=not rows,
        observed=len(rows),
        threshold=0,
        message=f"{len(rows)} duplicated player-weeks",
        samples=tuple(dict(row._mapping) for row in rows[:MAX_SAMPLES]),
    )


async def check_weeks_are_plausible(session: AsyncSession) -> CheckResult:
    """No observation should sit outside a real NFL week."""
    failures = 0
    samples: list[dict[str, Any]] = []
    for model, label in (
        (InjuryEvent, "injury_events"),
        (PlayerWeeklyStat, "player_weekly_stats"),
        (SnapCount, "snap_counts"),
    ):
        condition = (model.week < MIN_WEEK) | (model.week > MAX_WEEK)
        count = await _count(session, select(func.count()).select_from(model).where(condition))
        failures += count
        if count:
            samples.append({"table": label, "rows": count})

    return CheckResult(
        name="weeks_are_plausible",
        dataset="observations",
        severity=Severity.ERROR,
        passed=failures == 0,
        observed=failures,
        threshold=0,
        message=f"{failures} observations outside week {MIN_WEEK}-{MAX_WEEK}",
        samples=tuple(samples),
    )


async def check_negative_snap_counts(session: AsyncSession) -> CheckResult:
    """Snaps cannot be negative."""
    condition = SnapCount.offense_snaps < 0
    bad = await _count(session, select(func.count()).select_from(SnapCount).where(condition))
    return CheckResult(
        name="no_negative_snap_counts",
        dataset="snap_counts",
        severity=Severity.ERROR,
        passed=bad == 0,
        observed=bad,
        threshold=0,
        message=f"{bad} rows with negative snaps",
    )


async def check_adp_is_in_range(session: AsyncSession) -> CheckResult:
    """An ADP outside a draftable range is a mis-parsed column."""
    condition = (AdpSnapshot.adp <= 0) | (AdpSnapshot.adp > 600)
    bad = await _count(session, select(func.count()).select_from(AdpSnapshot).where(condition))
    samples = await _samples(
        session, select(AdpSnapshot.player_uuid, AdpSnapshot.adp).where(condition)
    )
    return CheckResult(
        name="adp_in_range",
        dataset="adp_snapshots",
        severity=Severity.ERROR,
        passed=bad == 0,
        observed=bad,
        threshold=0,
        message=f"{bad} ADP values outside 0-600",
        samples=samples,
    )


async def check_projections_are_in_range(session: AsyncSession) -> CheckResult:
    """A projection outside a rational range is a mis-parsed column."""
    condition = (FantasyProjection.projected_points < -50) | (
        FantasyProjection.projected_points > 700
    )
    bad = await _count(
        session, select(func.count()).select_from(FantasyProjection).where(condition)
    )
    samples = await _samples(
        session,
        select(FantasyProjection.player_uuid, FantasyProjection.projected_points).where(condition),
    )
    return CheckResult(
        name="projections_in_range",
        dataset="fantasy_projections",
        severity=Severity.ERROR,
        passed=bad == 0,
        observed=bad,
        threshold=0,
        message=f"{bad} projections outside -50 to 700",
        samples=samples,
    )


# Every check, in the order they are reported.
ALL_CHECKS = (
    check_player_pool_depth,
    check_positions_are_known,
    check_ages_are_plausible,
    check_identity_coverage,
    check_unresolved_conflicts,
    check_no_duplicate_weekly_stats,
    check_weeks_are_plausible,
    check_negative_snap_counts,
    check_adp_is_in_range,
    check_projections_are_in_range,
)


async def run_quality_checks(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    ingestion_run_id: int | None = None,
) -> tuple[CheckResult, ...]:
    """Run every check and persist the results.

    Returns:
        Every result, in declaration order. Failures are recorded rather than
        raised: a quality run reports on the whole dataset, and stopping at the
        first problem would hide the rest.
    """
    results: list[CheckResult] = []
    checked_at = utcnow()

    async with session_factory() as session:
        for check in ALL_CHECKS:
            results.append(await check(session))

    async with session_factory() as session:
        session.add_all(
            [
                DataQualityResult(
                    ingestion_run_id=ingestion_run_id,
                    check_name=result.name,
                    dataset=result.dataset,
                    severity=result.severity.value,
                    passed=result.passed,
                    observed_value=result.observed,
                    threshold=result.threshold,
                    failing_row_count=0 if result.passed else int(result.observed),
                    message=result.message,
                    sample_failures=({"rows": list(result.samples)} if result.samples else None),
                    checked_at=checked_at,
                )
                for result in results
            ]
        )
        await session.commit()

    blocking = [r for r in results if r.is_blocking]
    log.info(
        "quality_checks_complete",
        total=len(results),
        failed=sum(1 for r in results if not r.passed),
        blocking=len(blocking),
    )
    for result in blocking:
        log.error("quality_check_failed", check=result.name, message=result.message)

    return tuple(results)


def summarise(results: Sequence[CheckResult]) -> str:
    """Render results as a readable block, for the CLI."""
    lines: list[str] = []
    for result in results:
        mark = "ok  " if result.passed else ("FAIL" if result.is_blocking else "warn")
        lines.append(f"  [{mark}] {result.name:<32} {result.message}")
    return "\n".join(lines)
