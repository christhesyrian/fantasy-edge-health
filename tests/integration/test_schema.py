"""Schema smoke tests.

Verifies the models are internally consistent and that the SQLite fallback -
the path a reviewer with no Docker will actually take - really works.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import inspect

import fhe.db.models  # noqa: F401  -- registers every table on Base.metadata
from fhe.config import Settings
from fhe.db import Base, create_engine

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "players",
    "player_external_ids",
    "player_identity_conflicts",
    "teams",
    "seasons",
    "injury_events",
    "practice_reports",
    "current_player_health",
    "health_score_snapshots",
    "availability_predictions",
    "player_weekly_stats",
    "snap_counts",
    "depth_chart_snapshots",
    "fantasy_projections",
    "adp_snapshots",
    "fantasy_rankings",
    "fantasy_leagues",
    "drafts",
    "draft_slots",
    "draft_picks",
    "fantasy_rosters",
    "roster_players",
    "draft_recommendation_snapshots",
    "data_ingestion_runs",
    "data_quality_results",
    "provider_sync_state",
}


async def test_schema_creates_on_the_sqlite_fallback(tmp_path: Path) -> None:
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            tables = await conn.run_sync(lambda sync: set(inspect(sync).get_table_names()))
    finally:
        await engine.dispose()

    assert tables >= EXPECTED_TABLES


def test_every_declared_table_is_registered() -> None:
    """Guards against a model file that exists but is never imported."""
    assert set(Base.metadata.tables) >= EXPECTED_TABLES


def test_idempotency_constraints_exist() -> None:
    """The database-level guarantees behind safe live-draft ingestion."""
    picks = Base.metadata.tables["draft_picks"]
    constraint_names = {c.name for c in picks.constraints}
    assert "uq_pick_number_per_draft" in constraint_names
    assert "uq_player_once_per_draft" in constraint_names


def test_provenance_columns_are_present_on_time_sensitive_tables() -> None:
    """Every displayed metric must be able to state its source and age."""
    for table_name in (
        "injury_events",
        "practice_reports",
        "adp_snapshots",
        "fantasy_projections",
        "player_weekly_stats",
    ):
        columns = set(Base.metadata.tables[table_name].columns.keys())
        assert {"source", "ingested_at", "observed_at"} <= columns, table_name
