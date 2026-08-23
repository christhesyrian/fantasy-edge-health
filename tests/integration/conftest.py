"""Shared fixtures for integration tests.

The seeded database and the session registry live here rather than in one test
module because connecting a draft and recovering one need the identical
starting point — and importing a fixture across modules shadows it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.api.events import InProcessEventBus
from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.config import Settings
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.player import Player, PlayerExternalId

NOW = datetime(2026, 8, 22, tzinfo=UTC)

# Thirty running backs with a clean projection and ADP each. Enough for a
# twelve-team draft to have a real board, few enough to stay fast.
POOL_SIZE = 30


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A database seeded with a small canonical player pool."""
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)

    async with factory() as session:
        for index in range(POOL_SIZE):
            uuid = f"p-{index:03d}"
            session.add(
                Player(
                    player_uuid=uuid,
                    full_name=f"Player {index}",
                    normalized_name=f"player{index}",
                    position="RB",
                    team="SEA",
                    age=26.0,
                    years_experience=4,
                    is_active=True,
                    popularity_rank=index + 1,
                    identity_method="DIRECT_GSIS",
                    identity_confidence=1.0,
                    source="test",
                )
            )
            session.add(
                PlayerExternalId(player_uuid=uuid, system="sleeper_id", external_id=f"s-{index}")
            )
            session.add(
                FantasyProjection(
                    player_uuid=uuid,
                    season=2026,
                    week=SEASON_LONG_WEEK,
                    scoring_format="ppr",
                    projected_points=300.0 - index * 5,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
            session.add(
                AdpSnapshot(
                    player_uuid=uuid,
                    season=2026,
                    scoring_format="ppr",
                    adp=float(index + 1),
                    snapshot_date=NOW,
                    source="test",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
        await session.commit()

    yield factory
    await engine.dispose()


@pytest.fixture
def registry() -> DraftSessionRegistry:
    """A fresh in-memory session registry."""
    return DraftSessionRegistry(InProcessEventBus())
