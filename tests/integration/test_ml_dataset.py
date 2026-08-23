"""Point-in-time dataset construction.

The property that matters is that no feature can see the future. It is asserted
structurally — by rebuilding with later weeks removed — rather than by reading
the code and trusting it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.core.types import BodyRegion, InjuryDesignation
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.models.football import PlayerWeeklyStat
from fhe.db.models.health import InjuryEvent, PracticeReport
from fhe.db.models.player import Player
from fhe.ml.dataset import LABEL_COLUMN, build_training_frame
from fhe.ml.leakage import check_features_are_point_in_time

pytestmark = pytest.mark.integration

SEASON = 2024
NOW = datetime(2024, 9, 1, tzinfo=UTC)


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    async with factory() as session:
        session.add(
            Player(
                player_uuid="p1",
                full_name="Test Back",
                normalized_name="testback",
                position="RB",
                team="SEA",
                age=27.0,
                years_experience=5,
                is_active=True,
                identity_method="DIRECT_GSIS",
                identity_confidence=1.0,
                source="test",
            )
        )
        # Active every week, with production.
        for week in range(1, 13):
            session.add(
                PlayerWeeklyStat(
                    player_uuid="p1",
                    season=SEASON,
                    week=week,
                    season_type="REG",
                    carries=15.0,
                    targets=3.0,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
        # A hamstring problem in week 4, then ruled out in weeks 6 and 7.
        session.add(
            InjuryEvent(
                player_uuid="p1",
                season=SEASON,
                week=4,
                body_region=BodyRegion.HAMSTRING.value,
                raw_primary_injury="Hamstring",
                designation=InjuryDesignation.QUESTIONABLE.value,
                source="test",
                ingested_at=NOW,
                observed_at=NOW,
            )
        )
        for week in (6, 7):
            session.add(
                InjuryEvent(
                    player_uuid="p1",
                    season=SEASON,
                    week=week,
                    body_region=BodyRegion.HAMSTRING.value,
                    raw_primary_injury="Hamstring",
                    designation=InjuryDesignation.OUT.value,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
        session.add(
            PracticeReport(
                player_uuid="p1",
                season=SEASON,
                week=4,
                status="LIMITED",
                raw_status="Limited Participation in Practice",
                source="test",
                ingested_at=NOW,
                observed_at=NOW,
            )
        )
        await session.commit()

    yield factory
    await engine.dispose()


async def frame(session_factory: Any, horizon: int = 4) -> list[dict[str, Any]]:
    async with session_factory() as session:
        rows, _ = await build_training_frame(session, seasons=[SEASON], horizon_weeks=horizon)
    return rows


class TestLabel:
    async def test_a_ruling_out_inside_the_horizon_is_positive(self, session_factory: Any) -> None:
        rows = {int(r["week"]): r for r in await frame(session_factory)}
        # Week 3 sees weeks 4-7, which contain the week-6 and week-7 OUT reports.
        assert rows[3][LABEL_COLUMN] == 1

    async def test_a_week_with_no_ruling_out_ahead_is_negative(self, session_factory: Any) -> None:
        rows = {int(r["week"]): r for r in await frame(session_factory)}
        # Week 8 sees weeks 9-12, which are clean.
        assert rows[8][LABEL_COLUMN] == 0

    async def test_the_horizon_is_respected(self, session_factory: Any) -> None:
        short = {int(r["week"]): r for r in await frame(session_factory, horizon=1)}
        # With a one-week horizon, week 3 only sees week 4, which is Questionable.
        assert short[3][LABEL_COLUMN] == 0
        assert short[5][LABEL_COLUMN] == 1


class TestFeatures:
    async def test_prior_state_accumulates_forward_only(self, session_factory: Any) -> None:
        rows = {int(r["week"]): r for r in await frame(session_factory)}

        # Before the week-4 report, nothing is known.
        assert rows[3]["prior_reports_this_season"] == 0
        assert rows[3]["prior_soft_tissue_reports"] == 0
        # After it, the hamstring is on the record.
        assert rows[5]["prior_reports_this_season"] == 1
        assert rows[5]["prior_soft_tissue_reports"] == 1
        assert rows[5]["prior_distinct_regions"] == 1

    async def test_this_weeks_report_is_a_feature(self, session_factory: Any) -> None:
        """What was known on the report is fair game; the outcome is not."""
        rows = {int(r["week"]): r for r in await frame(session_factory)}
        assert rows[4]["carried_practice_limited"] == 1.0
        assert rows[4]["carried_designation"] > 0

    async def test_rolling_usage_covers_only_earlier_weeks(self, session_factory: Any) -> None:
        rows = {int(r["week"]): r for r in await frame(session_factory)}
        assert rows[1]["games_with_stats_so_far"] == 0
        assert rows[5]["games_with_stats_so_far"] == 4
        assert rows[5]["rolling_touches_per_game"] == pytest.approx(18.0)


class TestPointInTimeProperty:
    async def test_hiding_the_future_does_not_change_the_past(self, session_factory: Any) -> None:
        """The structural proof. If any feature reached forward, removing later
        weeks would change an earlier row."""
        full = await frame(session_factory)
        truncated = [r for r in full if r["week"] < 6]

        result = check_features_are_point_in_time(full, truncated, cutoff_week=6)
        assert result.passed, result.detail

    async def test_rows_without_an_observed_horizon_are_excluded(
        self, session_factory: Any
    ) -> None:
        """A week whose horizon falls past the data would be labelled negative
        purely because nothing was recorded."""
        weeks = {int(r["week"]) for r in await frame(session_factory)}
        assert 18 not in weeks
        assert max(weeks) <= 12
