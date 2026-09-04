"""Shared provider plumbing: errors, rate limiting, and retries.

Draft night is the worst possible time for a provider to wobble, so the
resilience primitives live here rather than being reimplemented per adapter.

Design notes
------------
* **Rate limiting is self-imposed and conservative.** Sleeper documents a limit
  of 1000 requests per minute and warns about IP blocking. Being blocked mid
  draft is unrecoverable within the session, so the limiter defaults well below
  the documented ceiling.
* **Retries use full jitter.** Exponential backoff without jitter synchronises
  every client into the same retry wave. Full jitter is the standard fix.
* **Only idempotent, transient failures are retried.** A 404 is an answer, not a
  wobble, and retrying it wastes the budget that a real outage needs.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

import httpx

from fhe.observability import PROVIDER_LATENCY, PROVIDER_REQUESTS, get_logger

log = get_logger(__name__)

# HTTP statuses worth retrying: transient server-side or throttling conditions.
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({408, 425, 429, 500, 502, 503, 504})


class ProviderError(Exception):
    """Base class for provider failures.

    Args:
        message: Human-readable description.
        provider: Which provider failed.
        operation: The logical operation attempted.
        status_code: HTTP status, when there was one.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        operation: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.operation = operation
        self.status_code = status_code

    @property
    def is_retryable(self) -> bool:
        """Whether retrying could plausibly succeed."""
        return self.status_code in RETRYABLE_STATUS if self.status_code else False


class ProviderTimeoutError(ProviderError):
    """The provider did not respond within the configured timeout."""

    @property
    def is_retryable(self) -> bool:
        """Timeouts are always worth one more attempt."""
        return True


class ProviderRateLimitError(ProviderError):
    """The provider signalled that we are sending too many requests."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        operation: str,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, provider=provider, operation=operation, status_code=429)
        self.retry_after_seconds = retry_after_seconds

    @property
    def is_retryable(self) -> bool:
        """Throttling clears with time."""
        return True


class ProviderUnavailableError(ProviderError):
    """The provider is unreachable or returned an unusable response."""


class ProviderDataError(ProviderError):
    """The provider responded, but the payload did not match its contract.

    Raised rather than coerced: a malformed response must never be allowed to
    overwrite known-good state.
    """


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff with full jitter.

    Args:
        max_attempts: Total attempts including the first.
        base_delay_seconds: Delay after the first failure.
        max_delay_seconds: Ceiling for any single sleep.
        multiplier: Growth factor between attempts.
    """

    max_attempts: int = 4
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    multiplier: float = 2.0

    def delay_for(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """Compute the sleep before ``attempt`` (1-indexed, so attempt 2 is the first retry).

        Full jitter: a uniform draw from ``[0, capped_backoff]``. This spreads
        retries instead of letting every client fire simultaneously.
        """
        if attempt <= 1:
            return 0.0
        raw = self.base_delay_seconds * (self.multiplier ** (attempt - 2))
        capped = min(self.max_delay_seconds, raw)
        source = rng or random
        return source.uniform(0.0, capped)


class RateLimiter:
    """Async token-bucket limiter.

    Args:
        max_per_minute: Sustained request ceiling.
        burst: Tokens available instantaneously. Defaults to one second's worth,
            which smooths a poll cycle without letting a bug empty the budget.
    """

    def __init__(self, max_per_minute: int, *, burst: int | None = None) -> None:
        if max_per_minute <= 0:
            raise ValueError("max_per_minute must be positive")
        self._rate_per_second = max_per_minute / 60.0
        self._capacity = float(burst if burst is not None else max(1, max_per_minute // 60))
        self._tokens = self._capacity
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request may be sent."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_second)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit / self._rate_per_second
            await asyncio.sleep(wait)


# One limiter per rate, shared by every provider in the process. Keyed on the
# rate rather than global so a test can ask for an isolated budget by asking for
# an unusual one, and so two providers with genuinely different ceilings do not
# share a bucket.
_SHARED_LIMITERS: dict[int, RateLimiter] = {}


def shared_rate_limiter(max_per_minute: int) -> RateLimiter:
    """The process-wide limiter for a given rate.

    A provider's rate limit is enforced by the provider, against an address, not
    against an object. Every instance holding its own budget means the process
    spends the sum of them.
    """
    limiter = _SHARED_LIMITERS.get(max_per_minute)
    if limiter is None:
        limiter = RateLimiter(max_per_minute)
        _SHARED_LIMITERS[max_per_minute] = limiter
    return limiter


async def with_retries[T](
    operation: str,
    provider: str,
    func: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    rng: random.Random | None = None,
) -> T:
    """Run ``func`` with retries, metrics, and structured logging.

    Only failures that report themselves as retryable are retried. The final
    failure is re-raised so callers can decide whether to degrade or fail.
    """
    retry_policy = policy or RetryPolicy()
    last_error: ProviderError | None = None

    for attempt in range(1, retry_policy.max_attempts + 1):
        delay = retry_policy.delay_for(attempt, rng=rng)
        if delay:
            await asyncio.sleep(delay)

        started = time.perf_counter()
        try:
            result = await func()
        except ProviderError as error:
            elapsed = time.perf_counter() - started
            PROVIDER_REQUESTS.labels(provider, operation, "error").inc()
            PROVIDER_LATENCY.labels(provider, operation).observe(elapsed)
            last_error = error
            if not error.is_retryable or attempt == retry_policy.max_attempts:
                log.warning(
                    "provider_call_failed",
                    provider=provider,
                    operation=operation,
                    attempt=attempt,
                    status_code=error.status_code,
                    retryable=error.is_retryable,
                    error=str(error),
                )
                raise
            log.info(
                "provider_call_retrying",
                provider=provider,
                operation=operation,
                attempt=attempt,
                status_code=error.status_code,
            )
            continue

        elapsed = time.perf_counter() - started
        PROVIDER_REQUESTS.labels(provider, operation, "success").inc()
        PROVIDER_LATENCY.labels(provider, operation).observe(elapsed)
        if attempt > 1:
            log.info(
                "provider_call_recovered",
                provider=provider,
                operation=operation,
                attempt=attempt,
            )
        return result

    # Unreachable: the loop either returns or raises.
    raise last_error or ProviderUnavailableError(
        "retry loop exhausted", provider=provider, operation=operation
    )


def classify_http_error(error: Exception, *, provider: str, operation: str) -> ProviderError:
    """Translate an httpx exception into the provider error hierarchy."""
    if isinstance(error, httpx.TimeoutException):
        return ProviderTimeoutError(
            f"{provider}.{operation} timed out", provider=provider, operation=operation
        )
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 429:
            header = error.response.headers.get("retry-after")
            retry_after: float | None = None
            if header:
                try:
                    retry_after = float(header)
                except ValueError:
                    # A date-formatted Retry-After is valid HTTP but rare here;
                    # falling back to normal backoff is safer than guessing.
                    retry_after = None
            return ProviderRateLimitError(
                f"{provider}.{operation} rate limited",
                provider=provider,
                operation=operation,
                retry_after_seconds=retry_after,
            )
        return ProviderUnavailableError(
            f"{provider}.{operation} returned HTTP {status}",
            provider=provider,
            operation=operation,
            status_code=status,
        )
    if isinstance(error, httpx.HTTPError):
        return ProviderUnavailableError(
            f"{provider}.{operation} transport failure: {error}",
            provider=provider,
            operation=operation,
        )
    return ProviderUnavailableError(
        f"{provider}.{operation} unexpected failure: {error}",
        provider=provider,
        operation=operation,
    )
