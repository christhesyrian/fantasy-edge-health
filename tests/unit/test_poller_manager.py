"""Poller supervision.

The manager owns poller lifetimes, so the properties that matter are about
*not* doing things twice and *always* cleaning up.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from fhe.api.events import InProcessEventBus
from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.api.services.poller_manager import (
    MAX_CONCURRENT_POLLERS,
    PollerManager,
    TooManyDraftsError,
)
from fhe.config import Settings
from fhe.core.draft.state import DraftState
from fhe.core.draft.vorp import compute_replacement_baseline
from fhe.core.league import LeagueSettings
from fhe.core.simulation import generate_player_pool
from fhe.data.providers.sleeper import SleeperDraft, SleeperPick
from fhe.worker.draft_poller import DraftBinding

pytestmark = pytest.mark.unit


class IdleProvider:
    """A provider that always reports the same, unchanging draft."""

    def __init__(self) -> None:
        self.calls = 0

    async def get_draft_picks(self, draft_id: str) -> tuple[SleeperPick, ...]:
        self.calls += 1
        return ()

    async def get_draft(self, draft_id: str) -> SleeperDraft:
        return SleeperDraft(
            draft_id=draft_id,
            league_id="l",
            status="drafting",
            draft_type="snake",
            season="2026",
            settings={"teams": 12, "rounds": 15},
            metadata={},
            draft_order={},
            slot_to_roster_id={},
        )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        draft_poll_interval_seconds=0.02,
        draft_poll_max_interval_seconds=0.05,
    )


@pytest.fixture
def registry() -> DraftSessionRegistry:
    return DraftSessionRegistry(InProcessEventBus())


@pytest.fixture(scope="module")
def pool() -> Any:
    return generate_player_pool()


def make_session(
    registry: DraftSessionRegistry, pool: Any, league: LeagueSettings, draft_id: str
) -> Any:
    """Register a live session for a draft id."""
    return registry.register_live(
        session_id=draft_id,
        league=league,
        pool=pool,
        state=DraftState(league, draft_id=draft_id),
        baseline=compute_replacement_baseline(pool, league),
    )


def binding_for(league: LeagueSettings, draft_id: str) -> DraftBinding:
    return DraftBinding(draft_id=draft_id, league=league, user_draft_slot=5)


class TestLifecycle:
    async def test_start_then_stop(
        self,
        settings: Settings,
        registry: DraftSessionRegistry,
        pool: Any,
        league: LeagueSettings,
    ) -> None:
        manager = PollerManager(settings, registry)
        session = make_session(registry, pool, league, "d1")

        manager.start(IdleProvider(), binding_for(league, "d1"), session)
        assert manager.count == 1
        assert "d1" in manager.active_draft_ids

        await asyncio.sleep(0.08)
        assert await manager.stop("d1") is True
        assert manager.count == 0

    async def test_starting_the_same_draft_twice_does_not_double_the_poll_rate(
        self,
        settings: Settings,
        registry: DraftSessionRegistry,
        pool: Any,
        league: LeagueSettings,
    ) -> None:
        """Two pollers on one draft would double the request rate against a
        provider whose limit is per IP."""
        manager = PollerManager(settings, registry)
        session = make_session(registry, pool, league, "d1")
        binding = binding_for(league, "d1")

        manager.start(IdleProvider(), binding, session)
        manager.start(IdleProvider(), binding, session)

        assert manager.count == 1
        await manager.stop("d1")

    async def test_stopping_an_unknown_draft_is_not_an_error(
        self, settings: Settings, registry: DraftSessionRegistry
    ) -> None:
        manager = PollerManager(settings, registry)
        assert await manager.stop("never-started") is False

    async def test_stop_all_clears_every_poller(
        self,
        settings: Settings,
        registry: DraftSessionRegistry,
        pool: Any,
        league: LeagueSettings,
    ) -> None:
        manager = PollerManager(settings, registry)
        for index in range(3):
            draft_id = f"d{index}"
            session = make_session(registry, pool, league, draft_id)
            manager.start(IdleProvider(), binding_for(league, draft_id), session)

        assert manager.count == 3
        await manager.stop_all()
        assert manager.count == 0

    async def test_concurrent_drafts_are_capped(
        self,
        settings: Settings,
        registry: DraftSessionRegistry,
        pool: Any,
        league: LeagueSettings,
    ) -> None:
        """The provider rate limit is per IP, so it is shared across drafts."""
        manager = PollerManager(settings, registry)
        try:
            for index in range(MAX_CONCURRENT_POLLERS):
                draft_id = f"d{index}"
                session = make_session(registry, pool, league, draft_id)
                manager.start(IdleProvider(), binding_for(league, draft_id), session)

            session = make_session(registry, pool, league, "one-too-many")
            with pytest.raises(TooManyDraftsError, match="safe limit"):
                manager.start(IdleProvider(), binding_for(league, "one-too-many"), session)
        finally:
            await manager.stop_all()


class TestStatus:
    async def test_status_is_available_while_running(
        self,
        settings: Settings,
        registry: DraftSessionRegistry,
        pool: Any,
        league: LeagueSettings,
    ) -> None:
        manager = PollerManager(settings, registry)
        session = make_session(registry, pool, league, "d1")
        manager.start(IdleProvider(), binding_for(league, "d1"), session)

        await asyncio.sleep(0.08)
        status = manager.status("d1")
        assert status is not None
        assert status.poll_count >= 1
        assert status.is_stale is False
        await manager.stop("d1")

    async def test_no_status_for_a_draft_that_is_not_followed(
        self, settings: Settings, registry: DraftSessionRegistry
    ) -> None:
        """A simulation has no poller, and must not pretend otherwise."""
        manager = PollerManager(settings, registry)
        assert manager.status("simulation-id") is None
