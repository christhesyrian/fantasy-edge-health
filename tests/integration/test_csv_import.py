"""Tests for the manual CSV import path.

This is the route that keeps the product useful with no paid API, and it is the
only ingestion fed by a user-supplied file, so its validation and its refusal to
guess are both load-bearing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.core.types import ScoringFormat
from fhe.data.ingest.csv_import import (
    MAX_ROWS,
    MAX_UPLOAD_BYTES,
    CsvImportError,
    ImportKind,
    import_adp_csv,
    import_projections_csv,
    read_csv_text,
)
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.player import Player, PlayerExternalId

pytestmark = pytest.mark.integration

SEASON = 2026


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    async with factory() as session:
        # Two distinct players, plus a deliberate same-name/same-position pair
        # on different teams so ambiguity can be exercised.
        for uuid, name, normalized, position, team in [
            ("u-chase", "Ja'Marr Chase", "jamarrchase", "WR", "CIN"),
            ("u-bijan", "Bijan Robinson", "bijanrobinson", "RB", "ATL"),
            ("u-mike-a", "Mike Williams", "mikewilliams", "WR", "NYJ"),
            ("u-mike-b", "Mike Williams", "mikewilliams", "WR", "PIT"),
        ]:
            session.add(
                Player(
                    player_uuid=uuid,
                    full_name=name,
                    normalized_name=normalized,
                    position=position,
                    team=team,
                    is_active=True,
                    identity_method="DIRECT_GSIS",
                    identity_confidence=1.0,
                    source="test",
                )
            )
        session.add(
            PlayerExternalId(player_uuid="u-chase", system="sleeper_id", external_id="7564")
        )
        await session.commit()

    yield factory
    await engine.dispose()


async def count(session_factory: Any, model: Any) -> int:
    """Row count for a model."""
    async with session_factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar() or 0


class TestStructuralValidation:
    def test_missing_required_column_is_refused_with_a_useful_message(self) -> None:
        with pytest.raises(CsvImportError, match="missing required column"):
            read_csv_text("player_name,position\nA,WR\n", ImportKind.ADP)

    def test_a_file_with_no_header_is_refused(self) -> None:
        with pytest.raises(CsvImportError, match="no header"):
            read_csv_text("", ImportKind.ADP)

    def test_oversized_file_is_refused_before_parsing(self) -> None:
        oversized = "player_name,position,adp\n" + ("x" * (MAX_UPLOAD_BYTES + 1))
        with pytest.raises(CsvImportError, match="byte limit"):
            read_csv_text(oversized, ImportKind.ADP)

    def test_too_many_rows_is_refused(self) -> None:
        body = "\n".join(f"Player {i},WR,{i + 1}" for i in range(MAX_ROWS + 5))
        with pytest.raises(CsvImportError, match="row limit"):
            read_csv_text(f"player_name,position,adp\n{body}\n", ImportKind.ADP)

    def test_header_matching_is_case_and_space_insensitive(self) -> None:
        rows = read_csv_text(" Player_Name , Position , ADP \nA,WR,1.0\n", ImportKind.ADP)
        assert rows[0]["player_name"] == "A"


class TestAdpImport:
    async def test_imports_and_records_provenance(self, session_factory: Any) -> None:
        text = (
            "player_name,position,team,adp,adp_stdev,sample_size\n"
            "Ja'Marr Chase,WR,CIN,1.8,0.9,4210\n"
            "Bijan Robinson,RB,ATL,3.1,1.4,4210\n"
        )
        run = await import_adp_csv(
            session_factory,
            text,
            source="my_league_export",
            season=SEASON,
            scoring_format=ScoringFormat.PPR,
        )

        assert run.rows_written == 2
        async with session_factory() as session:
            rows = (await session.execute(select(AdpSnapshot))).scalars().all()
        assert {r.source for r in rows} == {"my_league_export"}
        assert all(r.observed_at is not None for r in rows)
        by_uuid = {r.player_uuid: r for r in rows}
        assert by_uuid["u-chase"].adp == pytest.approx(1.8)
        assert by_uuid["u-chase"].adp_stdev == pytest.approx(0.9)

    async def test_matches_by_explicit_id_when_supplied(self, session_factory: Any) -> None:
        text = "player_name,position,adp,sleeper_id\nWrong Name Entirely,WR,4.0,7564\n"
        run = await import_adp_csv(session_factory, text, source="test", season=SEASON)

        assert run.rows_written == 1
        async with session_factory() as session:
            row = (await session.execute(select(AdpSnapshot))).scalar_one()
        assert row.player_uuid == "u-chase"

    async def test_unknown_explicit_id_is_rejected_not_name_matched(
        self, session_factory: Any
    ) -> None:
        """An id that does not resolve is an error, not a hint to fall back."""
        text = "player_name,position,adp,sleeper_id\nJa'Marr Chase,WR,4.0,999999\n"
        run = await import_adp_csv(session_factory, text, source="test", season=SEASON)

        assert run.rows_written == 0
        assert any(r["reason"] == "unknown_sleeper_id" for r in run.rejections)

    async def test_ambiguous_name_is_rejected_not_guessed(self, session_factory: Any) -> None:
        """Two Mike Williamses at WR must not be silently collapsed."""
        text = "player_name,position,adp\nMike Williams,WR,55.0\n"
        run = await import_adp_csv(session_factory, text, source="test", season=SEASON)

        assert run.rows_written == 0
        assert any(r["reason"] == "ambiguous_name_position" for r in run.rejections)

    async def test_team_disambiguates_a_shared_name(self, session_factory: Any) -> None:
        text = "player_name,position,team,adp\nMike Williams,WR,PIT,55.0\n"
        run = await import_adp_csv(session_factory, text, source="test", season=SEASON)

        assert run.rows_written == 1
        async with session_factory() as session:
            row = (await session.execute(select(AdpSnapshot))).scalar_one()
        assert row.player_uuid == "u-mike-b"

    @pytest.mark.parametrize("value", ["0", "-3", "1200", "not-a-number", ""])
    async def test_implausible_adp_is_rejected(self, session_factory: Any, value: str) -> None:
        text = f"player_name,position,team,adp\nJa'Marr Chase,WR,CIN,{value}\n"
        run = await import_adp_csv(session_factory, text, source="test", season=SEASON)

        assert run.rows_written == 0
        assert run.rows_rejected == 1
        assert await count(session_factory, AdpSnapshot) == 0

    async def test_unknown_player_is_reported(self, session_factory: Any) -> None:
        text = "player_name,position,adp\nNobody At All,WR,20.0\n"
        run = await import_adp_csv(session_factory, text, source="test", season=SEASON)

        assert any(r["reason"] == "no_matching_player" for r in run.rejections)
        assert run.rejections[0]["player_name"] == "Nobody At All"

    async def test_duplicate_player_in_one_file_is_rejected(self, session_factory: Any) -> None:
        text = "player_name,position,team,adp\nJa'Marr Chase,WR,CIN,1.8\nJa'Marr Chase,WR,CIN,2.4\n"
        run = await import_adp_csv(session_factory, text, source="test", season=SEASON)

        assert run.rows_written == 1
        assert any(r["reason"] == "duplicate_player_in_file" for r in run.rejections)

    async def test_reimporting_the_same_snapshot_is_idempotent(self, session_factory: Any) -> None:
        from fhe.db.base import utcnow

        stamp = utcnow()
        text = "player_name,position,team,adp\nJa'Marr Chase,WR,CIN,1.8\n"
        for _ in range(3):
            await import_adp_csv(
                session_factory, text, source="test", season=SEASON, snapshot_date=stamp
            )
        assert await count(session_factory, AdpSnapshot) == 1


class TestProjectionImport:
    async def test_imports_season_projections(self, session_factory: Any) -> None:
        text = (
            "player_name,position,team,projected_points,"
            "projected_points_low,projected_points_high\n"
            "Ja'Marr Chase,WR,CIN,312.4,241.0,388.5\n"
        )
        run = await import_projections_csv(
            session_factory,
            text,
            source="test",
            season=SEASON,
            scoring_format=ScoringFormat.PPR,
        )

        assert run.rows_written == 1
        async with session_factory() as session:
            row = (await session.execute(select(FantasyProjection))).scalar_one()
        assert row.week == SEASON_LONG_WEEK, "a season projection uses the sentinel week"
        assert row.projected_points == pytest.approx(312.4)
        assert row.projected_points_low == pytest.approx(241.0)

    async def test_weekly_projections_are_kept_separate(self, session_factory: Any) -> None:
        text = (
            "player_name,position,team,projected_points,week\n"
            "Ja'Marr Chase,WR,CIN,312.4,\n"
            "Ja'Marr Chase,WR,CIN,19.2,1\n"
        )
        run = await import_projections_csv(session_factory, text, source="test", season=SEASON)
        assert run.rows_written == 2
        assert await count(session_factory, FantasyProjection) == 2

    @pytest.mark.parametrize("value", ["40000", "-500", "junk"])
    async def test_implausible_projection_is_rejected(
        self, session_factory: Any, value: str
    ) -> None:
        text = f"player_name,position,team,projected_points\nJa'Marr Chase,WR,CIN,{value}\n"
        run = await import_projections_csv(session_factory, text, source="test", season=SEASON)
        assert run.rows_written == 0
        assert run.rows_rejected == 1

    async def test_is_idempotent(self, session_factory: Any) -> None:
        text = "player_name,position,team,projected_points\nJa'Marr Chase,WR,CIN,312.4\n"
        for _ in range(3):
            await import_projections_csv(session_factory, text, source="test", season=SEASON)
        assert await count(session_factory, FantasyProjection) == 1

    async def test_season_projections_dedupe_despite_having_no_week(
        self, session_factory: Any
    ) -> None:
        """Regression: a NULL week made the unique constraint never match.

        SQL treats NULL as never equal to NULL, so two season-long projections
        for the same player both satisfied the uniqueness key and ON CONFLICT
        never fired. The sentinel week is what makes this converge.
        """
        text = "player_name,position,team,projected_points\nJa'Marr Chase,WR,CIN,312.4\n"
        await import_projections_csv(session_factory, text, source="a", season=SEASON)
        await import_projections_csv(session_factory, text, source="a", season=SEASON)
        assert await count(session_factory, FantasyProjection) == 1

    async def test_a_later_import_updates_the_value(self, session_factory: Any) -> None:
        for points in ("300.0", "325.5"):
            await import_projections_csv(
                session_factory,
                f"player_name,position,team,projected_points\nJa'Marr Chase,WR,CIN,{points}\n",
                source="test",
                season=SEASON,
            )
        async with session_factory() as session:
            row = (await session.execute(select(FantasyProjection))).scalar_one()
        assert row.projected_points == pytest.approx(325.5)
