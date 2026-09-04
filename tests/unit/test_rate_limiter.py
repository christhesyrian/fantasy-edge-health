"""Provider rate limiting.

Sleeper counts requests against an address, not against an object. Being
IP-blocked mid-draft is the single worst failure this product can suffer, so the
thing that meters requests has to count what the provider counts.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from fhe.config import Settings
from fhe.data.providers.base import RateLimiter, shared_rate_limiter
from fhe.data.providers.sleeper import SleeperProvider

pytestmark = pytest.mark.unit


class TestSharing:
    def test_providers_in_one_process_share_a_budget(self) -> None:
        """The bug this closes.

        One provider is built per live draft and per request-scoped lookup, so
        twelve pollers each politely staying under the ceiling added up to
        twelve times the ceiling.
        """
        settings = Settings(_env_file=None)
        first = SleeperProvider(settings)
        second = SleeperProvider(settings)

        assert first._limiter is second._limiter

    def test_the_same_rate_returns_the_same_limiter(self) -> None:
        assert shared_rate_limiter(600) is shared_rate_limiter(600)

    def test_different_rates_do_not_share_a_bucket(self) -> None:
        """Two providers with genuinely different ceilings are not one budget."""
        assert shared_rate_limiter(600) is not shared_rate_limiter(601)

    def test_an_injected_limiter_wins(self) -> None:
        """So a test can hold an isolated budget."""
        own = RateLimiter(60)
        provider = SleeperProvider(Settings(_env_file=None), limiter=own)

        assert provider._limiter is own


class TestMetering:
    async def test_requests_within_the_burst_do_not_wait(self) -> None:
        limiter = RateLimiter(600, burst=5)

        started = time.monotonic()
        for _ in range(5):
            await limiter.acquire()

        assert time.monotonic() - started < 0.1

    async def test_exceeding_the_rate_waits_rather_than_failing(self) -> None:
        """Waiting is the correct failure mode.

        A refusal would surface as a dead board mid-draft; a wait degrades every
        follower smoothly to the aggregate ceiling and recovers on its own.
        """
        limiter = RateLimiter(600, burst=1)  # ten per second
        await limiter.acquire()

        started = time.monotonic()
        await limiter.acquire()
        waited = time.monotonic() - started

        assert waited > 0.05

    async def test_concurrent_callers_are_all_served(self) -> None:
        """Twelve pollers sharing one bucket must not deadlock on each other."""
        limiter = RateLimiter(6000, burst=2)

        await asyncio.wait_for(asyncio.gather(*(limiter.acquire() for _ in range(12))), timeout=5)
