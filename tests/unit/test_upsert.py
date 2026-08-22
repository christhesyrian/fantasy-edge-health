"""Tests for the dialect-aware upsert helper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import Settings
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.models.player import Player
from fhe.db.upsert import UnsupportedDialectError, _insert_for, table_of, upsert_rows

pytestmark = pytest.mark.integration


def player_row(uuid: str, name: str, **overrides: Any) -> dict[str, Any]:
    """Minimal valid players row."""
    row: dict[str, Any] = {
        "player_uuid": uuid,
        "full_name": name,
        "normalized_name": name.lower().replace(" ", ""),
        "position": "RB",
        "team": "SEA",
        "is_active": True,
        "identity_method": "DIRECT_GSIS",
        "identity_confidence": 1.0,
        "source": "test",
    }
    row.update(overrides)
    return row


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


class TestUpsertSemantics:
    async def test_insert_then_update_converges(self, session_factory: Any) -> None:
        async with session_factory() as session:
            await upsert_rows(
                session,
                Player,
                [player_row("u1", "First Name")],
                conflict_columns=["player_uuid"],
            )
            await session.commit()

        async with session_factory() as session:
            await upsert_rows(
                session,
                Player,
                [player_row("u1", "Updated Name", team="KC")],
                conflict_columns=["player_uuid"],
            )
            await session.commit()

        async with session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(Player))).scalar()
            row = (
                await session.execute(select(Player).where(Player.player_uuid == "u1"))
            ).scalar_one()

        assert count == 1, "upsert duplicated instead of updating"
        assert row.full_name == "Updated Name"
        assert row.team == "KC"

    async def test_replaying_the_same_batch_is_a_no_op(self, session_factory: Any) -> None:
        rows = [player_row(f"u{i}", f"Player {i}") for i in range(30)]
        for _ in range(3):
            async with session_factory() as session:
                await upsert_rows(session, Player, rows, conflict_columns=["player_uuid"])
                await session.commit()

        async with session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(Player))).scalar()
        assert count == 30

    async def test_large_batch_is_chunked_under_the_parameter_limit(
        self, session_factory: Any
    ) -> None:
        """SQLite caps bound parameters; a wide table times a big batch exceeds it."""
        rows = [player_row(f"b{i}", f"Bulk {i}") for i in range(500)]
        async with session_factory() as session:
            written = await upsert_rows(session, Player, rows, conflict_columns=["player_uuid"])
            await session.commit()

        async with session_factory() as session:
            count = (await session.execute(select(func.count()).select_from(Player))).scalar()
        assert written == 500
        assert count == 500

    async def test_empty_input_is_a_no_op(self, session_factory: Any) -> None:
        async with session_factory() as session:
            assert await upsert_rows(session, Player, [], conflict_columns=["player_uuid"]) == 0

    async def test_heterogeneous_rows_are_rejected(self, session_factory: Any) -> None:
        """Mismatched keys would silently null out whichever column is missing."""
        rows = [player_row("u1", "A"), {"player_uuid": "u2", "full_name": "B"}]
        async with session_factory() as session:
            with pytest.raises(ValueError, match="heterogeneous"):
                await upsert_rows(session, Player, rows, conflict_columns=["player_uuid"])

    async def test_created_at_is_not_rewritten_by_a_later_ingestion(
        self, session_factory: Any
    ) -> None:
        async with session_factory() as session:
            await upsert_rows(
                session, Player, [player_row("u1", "A")], conflict_columns=["player_uuid"]
            )
            await session.commit()
        async with session_factory() as session:
            first = (
                await session.execute(select(Player).where(Player.player_uuid == "u1"))
            ).scalar_one()
            original_created = first.created_at

        async with session_factory() as session:
            await upsert_rows(
                session, Player, [player_row("u1", "B")], conflict_columns=["player_uuid"]
            )
            await session.commit()
        async with session_factory() as session:
            second = (
                await session.execute(select(Player).where(Player.player_uuid == "u1"))
            ).scalar_one()

        assert second.created_at == original_created


class TestDialectSelection:
    def test_unknown_dialect_raises_rather_than_racing(self) -> None:
        """A silent read-modify-write fallback would be a lost-update bug."""
        with pytest.raises(UnsupportedDialectError, match="mysql"):
            _insert_for("mysql", table_of(Player))

    def test_table_of_returns_the_mapped_table(self) -> None:
        assert table_of(Player).name == "players"
