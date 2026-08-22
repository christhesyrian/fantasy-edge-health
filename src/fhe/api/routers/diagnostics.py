"""Developer diagnostics.

Answers "is the data behind this board trustworthy?" as a query rather than a
guess. Every ingestion writes lineage, every unresolved player is recorded, and
this endpoint surfaces both.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from fhe.api.deps import SessionDep
from fhe.api.schemas import IngestionRunOut, PipelineHealth
from fhe.db.models.pipeline import DataIngestionRun, DataQualityResult
from fhe.db.models.player import Player, PlayerIdentityConflict

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("/pipeline", response_model=PipelineHealth, summary="Pipeline health")
async def pipeline_health(
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=200),
) -> PipelineHealth:
    """Recent ingestion runs, plus the counts that indicate data trouble.

    Unresolved identity conflicts are surfaced because a rising count means the
    player universe is drifting away from the historical data, which degrades
    every health signal quietly rather than loudly.
    """
    runs = (
        (
            await session.execute(
                select(DataIngestionRun).order_by(DataIngestionRun.started_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )

    conflicts = (
        await session.execute(
            select(func.count())
            .select_from(PlayerIdentityConflict)
            .where(PlayerIdentityConflict.resolved.is_(False))
        )
    ).scalar() or 0

    players = (await session.execute(select(func.count()).select_from(Player))).scalar() or 0

    failing = (
        await session.execute(
            select(func.count())
            .select_from(DataQualityResult)
            .where(DataQualityResult.passed.is_(False))
        )
    ).scalar() or 0

    return PipelineHealth(
        recent_runs=[
            IngestionRunOut(
                id=run.id,
                provider=run.provider,
                dataset=run.dataset,
                status=run.status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                duration_seconds=run.duration_seconds,
                rows_read=run.rows_read,
                rows_written=run.rows_written,
                rows_rejected=run.rows_rejected,
                rows_unresolved_identity=run.rows_unresolved_identity,
                error_category=run.error_category,
            )
            for run in runs
        ],
        unresolved_identity_conflicts=conflicts,
        players_tracked=players,
        failing_checks=failing,
    )
