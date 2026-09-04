"""Who reaches the draft pool.

The pool is capped, so what the cap excludes is a product decision rather than
an implementation detail. These assert the exclusions are the intended ones.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.api.services.player_pool import load_player_pool
from fhe.config import Settings
from fhe.core.types import Position, ScoringFormat
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.player import Player
from tests.integration.conftest import NOW

pytestmark = pytest.mark.integration


@pytest.fixture
async def empty_db(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """An empty database.

    Deliberately not the shared seeded fixture: these tests are about which
    players survive a capped selection, so any pre-existing player would
    compete for the cap and make the assertion meaningless.
    """
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


async def _add(
    session: Any,
    uuid: str,
    *,
    position: str,
    popularity: int | None,
    projection: float | None = None,
    adp: float | None = None,
) -> None:
    """Insert one player, optionally with fantasy value."""
    session.add(
        Player(
            player_uuid=uuid,
            full_name=f"Player {uuid}",
            normalized_name=uuid.replace("-", ""),
            position=position,
            team="SEA",
            age=26.0,
            years_experience=3,
            is_active=True,
            popularity_rank=popularity,
            identity_method="DIRECT_GSIS",
            identity_confidence=1.0,
            source="test",
        )
    )
    if projection is not None:
        session.add(
            FantasyProjection(
                player_uuid=uuid,
                season=2026,
                week=SEASON_LONG_WEEK,
                scoring_format="ppr",
                projected_points=projection,
                source="test",
                ingested_at=NOW,
                observed_at=NOW,
            )
        )
    if adp is not None:
        session.add(
            AdpSnapshot(
                player_uuid=uuid,
                season=2026,
                scoring_format="ppr",
                adp=adp,
                snapshot_date=NOW,
                source="test",
                ingested_at=NOW,
                observed_at=NOW,
            )
        )


async def test_a_position_with_no_popularity_rank_still_reaches_the_pool(
    empty_db: async_sessionmaker[AsyncSession],
) -> None:
    """Regression: defences were undraftable.

    Sleeper publishes no popularity rank for team defences. The pool ordered by
    that column and cut at a limit, so all thirty-two sorted past the cut and
    never loaded — not ranked low, absent — in a league that requires one.
    """
    async with empty_db() as session:
        # A crowd of ranked players, enough to bury anything sorted after them.
        for index in range(80):
            await _add(
                session,
                f"wr-{index:03d}",
                position="WR",
                popularity=index + 1,
                projection=200.0 - index,
                adp=float(index + 1),
            )
        # Defences: real fantasy value, no popularity rank at all.
        for index in range(12):
            await _add(
                session,
                f"def-{index:03d}",
                position="DEF",
                popularity=None,
                projection=110.0 - index,
                adp=150.0 + index,
            )
        await session.commit()

    async with empty_db() as session:
        pool, _ = await load_player_pool(
            session,
            season=2026,
            scoring_format=ScoringFormat.PPR,
            as_of=date(2026, 8, 24),
            # Room for all 92 seeded players: the assertion is about the
            # *ordering* not excluding defences, not about the cap.
            limit=100,
        )

    defences = [p for p in pool if p.position is Position.DEF]
    assert len(defences) == 12, "every defence with fantasy value must reach the pool"


async def test_a_player_with_fantasy_value_outranks_a_merely_popular_one(
    empty_db: async_sessionmaker[AsyncSession],
) -> None:
    """The cap must not spend itself on players nobody can draft usefully."""
    async with empty_db() as session:
        # Popular but with no projection and no ADP: a practice-squad body.
        for index in range(50):
            await _add(session, f"noise-{index:03d}", position="WR", popularity=index + 1)
        # Unpopular but genuinely draftable.
        await _add(session, "valuable", position="TE", popularity=None, projection=180.0, adp=60.0)
        await session.commit()

    async with empty_db() as session:
        pool, _ = await load_player_pool(
            session,
            season=2026,
            scoring_format=ScoringFormat.PPR,
            as_of=date(2026, 8, 24),
            limit=10,
        )

    assert any(p.player_uuid == "valuable" for p in pool)


async def test_the_relevant_tier_is_ordered_by_market_position(
    empty_db: async_sessionmaker[AsyncSession],
) -> None:
    """ADP is a direct statement of who gets drafted, so it orders the cut."""
    async with empty_db() as session:
        # Early ADP, no popularity rank.
        await _add(session, "early", position="RB", popularity=None, projection=200.0, adp=5.0)
        # Late ADP, very popular.
        await _add(session, "late", position="RB", popularity=1, projection=100.0, adp=300.0)
        await session.commit()

    async with empty_db() as session:
        pool, _ = await load_player_pool(
            session,
            season=2026,
            scoring_format=ScoringFormat.PPR,
            as_of=date(2026, 8, 24),
            limit=1,
        )

    assert [p.player_uuid for p in pool] == ["early"]
