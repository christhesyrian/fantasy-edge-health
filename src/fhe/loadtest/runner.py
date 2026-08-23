"""A small load and soak harness for the API's read paths.

Why not Locust or k6
--------------------
Both are good tools and both are the wrong trade here. k6 is a separate Go
binary a contributor has to install before `make` works, and Locust brings
Flask, gevent, and a web UI into a project whose entire runtime dependency on
HTTP is already `httpx`. What is actually needed is a few hundred concurrent
reads with honest percentiles, which is a page of asyncio.

What it measures
----------------
The paths that matter on draft night, in the proportion they are actually used:
the board is re-read after every pick, player detail opens constantly, and the
health endpoint is polled by whatever is running the container. Writes are
excluded on purpose — a draft's write rate is bounded by how fast twelve humans
can pick, which is not a load-testing problem.

Percentiles are reported from every observation rather than a running average,
because a mean latency hides exactly the stall a person notices.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx

from fhe.observability import get_logger

log = get_logger(__name__)

# Long enough that connection setup is not most of the measurement, short
# enough that a hung endpoint fails the run rather than stalling it.
REQUEST_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True, slots=True)
class Scenario:
    """One endpoint under test, and how much of the traffic it represents."""

    name: str
    path: str
    weight: int = 1


@dataclass
class ScenarioResult:
    """Latency and error observations for one scenario."""

    name: str
    path: str
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0
    status_counts: dict[int, int] = field(default_factory=dict)

    @property
    def count(self) -> int:
        """Successful observations."""
        return len(self.latencies_ms)

    def percentile(self, fraction: float) -> float:
        """Latency at a percentile, in milliseconds."""
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, int(fraction * len(ordered)))
        return ordered[index]

    @property
    def mean_ms(self) -> float:
        """Mean latency, reported only beside the percentiles."""
        return statistics.fmean(self.latencies_ms) if self.latencies_ms else 0.0


@dataclass
class LoadResult:
    """The whole run."""

    concurrency: int
    duration_seconds: float
    scenarios: list[ScenarioResult]

    @property
    def total_requests(self) -> int:
        """Every successful request across scenarios."""
        return sum(scenario.count for scenario in self.scenarios)

    @property
    def total_errors(self) -> int:
        """Every failed request across scenarios."""
        return sum(scenario.errors for scenario in self.scenarios)

    @property
    def throughput_rps(self) -> float:
        """Successful requests per second."""
        return self.total_requests / self.duration_seconds if self.duration_seconds else 0.0

    def render(self) -> str:
        """A fixed-width report suitable for pasting into a document."""
        lines = [
            f"concurrency {self.concurrency}   "
            f"duration {self.duration_seconds:.1f}s   "
            f"throughput {self.throughput_rps:.0f} req/s   "
            f"errors {self.total_errors}",
            "",
            f"{'scenario':<26}{'n':>7}{'mean':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}",
        ]
        for scenario in self.scenarios:
            lines.append(
                f"{scenario.name:<26}{scenario.count:>7}"
                f"{scenario.mean_ms:>8.1f}m"
                f"{scenario.percentile(0.50):>8.1f}m"
                f"{scenario.percentile(0.95):>8.1f}m"
                f"{scenario.percentile(0.99):>8.1f}m"
                f"{max(scenario.latencies_ms, default=0.0):>8.1f}m"
            )
        return "\n".join(lines)


def _weighted(scenarios: Sequence[Scenario]) -> list[Scenario]:
    """Expand weights into a flat list a worker can cycle through."""
    expanded: list[Scenario] = []
    for scenario in scenarios:
        expanded.extend([scenario] * max(1, scenario.weight))
    return expanded


async def _worker(
    client: httpx.AsyncClient,
    scenarios: list[Scenario],
    results: dict[str, ScenarioResult],
    deadline: float,
    offset: int,
) -> None:
    """Issue requests until the deadline, cycling through the scenario mix."""
    index = offset
    while time.monotonic() < deadline:
        scenario = scenarios[index % len(scenarios)]
        index += 1
        result = results[scenario.name]
        started = time.perf_counter()
        try:
            response = await client.get(scenario.path)
        except (httpx.HTTPError, OSError):
            # A refused or timed-out request is a result, not a crash: the
            # point of the run is to find where that starts happening.
            result.errors += 1
            continue
        elapsed_ms = (time.perf_counter() - started) * 1000
        result.status_counts[response.status_code] = (
            result.status_counts.get(response.status_code, 0) + 1
        )
        if response.status_code >= 400:
            result.errors += 1
        else:
            result.latencies_ms.append(elapsed_ms)


async def run_load_test(
    base_url: str,
    scenarios: Sequence[Scenario],
    *,
    concurrency: int = 20,
    duration_seconds: float = 15.0,
) -> LoadResult:
    """Drive `concurrency` clients against `base_url` for `duration_seconds`."""
    results = {
        scenario.name: ScenarioResult(name=scenario.name, path=scenario.path)
        for scenario in scenarios
    }
    mix = _weighted(scenarios)
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)

    started = time.monotonic()
    deadline = started + duration_seconds
    async with httpx.AsyncClient(
        base_url=base_url, timeout=REQUEST_TIMEOUT_SECONDS, limits=limits
    ) as client:
        await asyncio.gather(
            *(_worker(client, mix, results, deadline, offset) for offset in range(concurrency))
        )
    elapsed = time.monotonic() - started

    log.info(
        "load_test_complete",
        concurrency=concurrency,
        duration_seconds=round(elapsed, 2),
        requests=sum(r.count for r in results.values()),
        errors=sum(r.errors for r in results.values()),
    )
    return LoadResult(
        concurrency=concurrency,
        duration_seconds=elapsed,
        scenarios=list(results.values()),
    )


def default_scenarios(draft_id: str, player_uuid: str | None) -> list[Scenario]:
    """The read mix a live war room actually produces.

    Weighted from the client's own behaviour: every pick triggers one board
    re-read, the drawer is opened far less often than the board refreshes, and
    the state endpoint is polled alongside it.
    """
    scenarios = [
        Scenario("board (depth 120)", f"/api/v1/drafts/{draft_id}/board?depth=120", weight=6),
        Scenario("board (depth 40)", f"/api/v1/drafts/{draft_id}/board?depth=40", weight=3),
        Scenario("draft state", f"/api/v1/drafts/{draft_id}", weight=3),
        Scenario("health", "/api/v1/health", weight=1),
    ]
    if player_uuid:
        scenarios.append(
            Scenario("player detail", f"/api/v1/drafts/{draft_id}/players/{player_uuid}", weight=2)
        )
    return scenarios
