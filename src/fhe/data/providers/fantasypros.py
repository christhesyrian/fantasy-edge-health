"""FantasyPros API client.

Licensed, personal, and rate-limited
-----------------------------------
Unlike Sleeper and nflverse, this provider is **not** public data. Access is
governed by the FantasyPros API Terms of Use, and several of its clauses are
implemented here rather than left to the operator to remember:

* **"one API call per second and up to 100 API calls per day."** Both ceilings
  are enforced. The daily count is *persisted to disk*, so restarting the
  process cannot silently reset it and take the account over its licence.
* **"You should take steps to cache data on your end so that your application
  does not poll our APIs unnecessarily."** Every response is cached, and a
  cached response never spends a call.
* **"you are not licensed to use any Data that constitutes historical player
  statistics"** — so `/nfl/{season}/player-points` is deliberately not
  implemented, and nothing here stores a historical stat. Season projections
  and consensus rankings are forecasts and opinions, not history. Workload
  history continues to come from nflverse, which is public data.
* **"You must keep your API key strictly confidential."** The key is read from
  the environment, never logged, never written to the cache, and never included
  in an error message.
* **Attribution.** Every row imported through this provider carries the source
  name below, which the war room displays beside the numbers it derives.

Contract verified on 2026-08-23 against the published spec at
``https://api.fantasypros.com/public/v2/docs/fantasypros_v2_public.yml``:
base URL, the ``x-api-key`` header, and the two endpoints used. The *shape of
the per-player ``stats`` object is not described in that spec*, so it is parsed
defensively and anything unrecognised is reported rather than guessed at.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Self

import httpx

from fhe.config import Settings
from fhe.data.providers.base import (
    ProviderDataError,
    ProviderError,
    ProviderUnavailableError,
    classify_http_error,
)
from fhe.observability import get_logger

log = get_logger(__name__)

# The name recorded against every imported value, and shown in the war room.
# The Terms of Use require work based on this Data to say where it came from.
SOURCE: Final = "FantasyPros"

# Ranking type that yields average draft position rather than expert rank.
ADP_RANKING_TYPE: Final = "ADP"

# Which `stats` field carries fantasy points for each scoring family.
_POINTS_FIELD_BY_FORMAT: Final[dict[str, str]] = {
    "ppr": "points_ppr",
    "half_ppr": "points_half",
    "standard": "points",
}

_SCORING_BY_FORMAT: Final[dict[str, str]] = {
    "ppr": "PPR",
    "half_ppr": "HALF",
    "standard": "STD",
}

# Positions worth pulling for a draft board. Deliberately not "ALL": every
# extra position is another call against a 100-per-day budget.
DRAFT_POSITIONS: Final[tuple[str, ...]] = ("QB", "RB", "WR", "TE", "K", "DST")


class FantasyProsNotConfiguredError(ProviderError):
    """No API key is set, so the adapter stays disabled rather than guessing."""

    def __init__(self) -> None:
        super().__init__(
            "No FantasyPros API key configured. Set FHE_FANTASYPROS_API_KEY in .env. "
            "Without it this provider stays disabled and the product falls back to "
            "CSV import.",
            provider=SOURCE,
            operation="configure",
        )

    @property
    def is_retryable(self) -> bool:
        """Never: a missing key is a configuration fact, not a transient fault."""
        return False


class FantasyProsQuotaExceededError(ProviderError):
    """The licensed daily call budget is spent."""

    def __init__(self, used: int, limit: int) -> None:
        super().__init__(
            f"FantasyPros daily API budget spent ({used}/{limit} calls). The licence "
            "allows 100 calls per day; the counter resets at midnight UTC. Cached "
            "responses still work.",
            provider=SOURCE,
            operation="quota",
        )

    @property
    def is_retryable(self) -> bool:
        """Not today. Retrying would knowingly exceed the licence."""
        return False


@dataclass
class _DailyBudget:
    """A call counter that survives a restart.

    Held on disk because the limit is a licence term rather than a performance
    concern: an in-memory counter would reset every time the process restarted,
    and the account would drift over its allowance without anyone noticing.
    """

    path: Path
    limit: int
    _day: str = ""
    _used: int = 0
    _loaded: bool = False

    def _today(self) -> str:
        return datetime.now(UTC).date().isoformat()

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._day = str(raw.get("day", ""))
            self._used = int(raw.get("used", 0))
        except (OSError, ValueError, TypeError):
            # A missing or corrupt counter starts the day at zero. Erring
            # toward zero risks a small overshoot once; erring toward the limit
            # would disable a paid-for integration on a bad file read.
            self._day, self._used = "", 0

    @property
    def used_today(self) -> int:
        """Calls spent so far today."""
        self._load()
        return self._used if self._day == self._today() else 0

    @property
    def remaining(self) -> int:
        """Calls still available today."""
        return max(0, self.limit - self.used_today)

    def spend(self, count: int = 1) -> None:
        """Record calls against today's budget."""
        self._load()
        today = self._today()
        self._used = (self._used if self._day == today else 0) + count
        self._day = today
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"day": self._day, "used": self._used}), encoding="utf-8"
            )
        except OSError:
            # Losing the counter costs accuracy, not correctness; the in-memory
            # value still guards this process.
            log.warning("fantasypros_budget_not_persisted", path=str(self.path))


@dataclass(frozen=True, slots=True)
class ProjectedPlayer:
    """One player's season projection, as published."""

    provider_player_id: str
    name: str
    team: str
    position: str
    projected_points: float | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RankedPlayer:
    """One player's consensus draft position."""

    provider_player_id: str
    name: str
    team: str
    position: str
    rank: float | None
    rank_stdev: float | None
    tier: int | None
    bye_week: int | None
    raw: dict[str, Any] = field(default_factory=dict)


def _clean(value: Any) -> str:
    """A trimmed string, or empty. Provider fields are inconsistently typed."""
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    """Parse a number the provider may send as a string, or return None."""
    text = _clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _integer(value: Any) -> int | None:
    """Parse an integer, or return None."""
    number = _number(value)
    return int(number) if number is not None else None


class FantasyProsProvider:
    """Typed, rate-limited, cached access to the licensed endpoints."""

    def __init__(self, settings: Settings, *, cache_dir: Path | None = None) -> None:
        self._settings = settings
        self._base = settings.fantasypros_base_url.rstrip("/")
        self._cache = cache_dir or (settings.data_dir / "cache" / "fantasypros")
        self._budget = _DailyBudget(
            path=self._cache / "daily_budget.json",
            limit=settings.fantasypros_max_calls_per_day,
        )
        self._client: httpx.AsyncClient | None = None
        self._last_call_monotonic: float | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        if not self._settings.fantasypros_enabled:
            raise FantasyProsNotConfiguredError
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=self._settings.fantasypros_timeout_seconds,
            # The key travels only in this header, only over TLS.
            headers={"x-api-key": self._settings.fantasypros_api_key},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def calls_remaining_today(self) -> int:
        """How much of the licensed daily budget is left."""
        return self._budget.remaining

    def _cache_path(self, key: str) -> Path:
        return self._cache / f"{key}.json"

    def _read_cache(self, key: str) -> dict[str, Any] | None:
        """A cached payload if it is still fresh, else None."""
        path = self._cache_path(key)
        try:
            stat = path.stat()
        except OSError:
            return None
        age_hours = (time.time() - stat.st_mtime) / 3600
        if age_hours > self._settings.fantasypros_cache_hours:
            return None
        try:
            payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        log.info("fantasypros_cache_hit", key=key, age_hours=round(age_hours, 2))
        return payload

    def _write_cache(self, key: str, payload: dict[str, Any]) -> None:
        path = self._cache_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            log.warning("fantasypros_cache_not_written", key=key)

    async def _get(self, path: str, params: dict[str, Any], *, cache_key: str) -> dict[str, Any]:
        """One licensed call, cached and rate-limited.

        The cache is checked before the budget on purpose: a cached answer costs
        nothing and must stay available even when the day's calls are spent.
        """
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        if self._client is None:
            raise ProviderUnavailableError(
                "client used outside its context manager", provider=SOURCE, operation=path
            )
        if self._budget.remaining <= 0:
            raise FantasyProsQuotaExceededError(
                self._budget.used_today, self._settings.fantasypros_max_calls_per_day
            )

        async with self._lock:
            # One call per second, measured from the previous call rather than
            # a fixed sleep, so a slow response does not add to the wait.
            minimum = self._settings.fantasypros_min_seconds_between_calls
            if self._last_call_monotonic is not None:
                elapsed = time.monotonic() - self._last_call_monotonic
                if elapsed < minimum:
                    await asyncio.sleep(minimum - elapsed)
            try:
                response = await self._client.get(path, params=params)
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise classify_http_error(error, provider=SOURCE, operation=path) from error
            finally:
                # Spent whether or not the response was usable: the provider
                # counted it either way.
                self._last_call_monotonic = time.monotonic()
                self._budget.spend()

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise ProviderDataError(
                f"FantasyPros returned a non-JSON body for {path}",
                provider=SOURCE,
                operation=path,
            ) from error

        self._write_cache(cache_key, payload)
        log.info(
            "fantasypros_call",
            path=path,
            remaining_today=self._budget.remaining,
        )
        self._warn_if_truncated(path, payload)
        return payload

    def _warn_if_truncated(self, path: str, payload: dict[str, Any]) -> None:
        """Say so when the account tier returned a fraction of the data.

        Verified 2026-08-23: a free-tier key returns `limit: 10` alongside a
        `count` of the full result set — 10 of 83 quarterbacks, 10 of 660
        ranked players. Silently importing a tenth of a draft board would be
        the worst kind of failure here: the board would look populated and run
        out mid-draft. So the truncation is reported, loudly, every time.
        """
        if not payload.get("public_api_limited"):
            return
        returned = len(payload.get("players") or [])
        available = _integer(payload.get("count"))
        log.warning(
            "fantasypros_response_truncated_by_tier",
            path=path,
            tier=_clean(payload.get("tier")) or "unknown",
            returned=returned,
            available=available,
            detail=(
                "This API key's tier returns a capped slice of each result set. "
                "Use the CSV export path for a full draft board."
            ),
        )

    async def get_projections(
        self, season: int, position: str, *, scoring_format: str = "ppr"
    ) -> tuple[ProjectedPlayer, ...]:
        """Season projections for one position.

        Season-long only: no ``week`` parameter is sent, because a draft board
        values a whole season.
        """
        scoring = _SCORING_BY_FORMAT.get(scoring_format.lower(), "PPR")
        payload = await self._get(
            f"/nfl/{season}/projections",
            {"position": position, "scoring": scoring},
            cache_key=f"projections-{season}-{position}-{scoring}",
        )
        players = payload.get("players")
        if not isinstance(players, list):
            raise ProviderDataError(
                f"projections payload for {position} has no players array",
                provider=SOURCE,
                operation="get_projections",
            )
        return tuple(
            self._to_projection(row, position, scoring_format)
            for row in players
            if isinstance(row, dict)
        )

    def _to_projection(
        self, row: dict[str, Any], requested_position: str, scoring_format: str = "ppr"
    ) -> ProjectedPlayer:
        """Map one projection row.

        The published spec does not describe the ``stats`` object, so the
        fantasy-points field is located by trying the names the API is known to
        use and falling back to ``None`` rather than inventing a number. A
        player with no recognisable projection is imported as unknown, which
        lowers confidence instead of fabricating value.
        """
        stats = row.get("stats")
        stats = stats if isinstance(stats, dict) else {}
        # Verified 2026-08-23: the response echoes `scoring: STD` even when PPR
        # is requested, but the stats object carries every format side by side.
        # Reading the format-specific field is therefore more trustworthy than
        # the request parameter. `points` is the last resort, not the first.
        preferred = _POINTS_FIELD_BY_FORMAT.get(scoring_format.lower(), "points_ppr")
        points: float | None = None
        for key in (preferred, "points", "fpts", "fantasy_points", "proj_pts"):
            if key in stats:
                points = _number(stats[key])
                if points is not None:
                    break
        return ProjectedPlayer(
            provider_player_id=_clean(row.get("fpid")),
            name=_clean(row.get("name")),
            team=_clean(row.get("team_id")).upper(),
            position=_clean(row.get("position_id")).upper() or requested_position,
            projected_points=points,
            raw=stats,
        )

    async def get_adp(
        self, season: int, position: str = "ALL", *, scoring_format: str = "ppr"
    ) -> tuple[RankedPlayer, ...]:
        """Consensus average draft position."""
        scoring = _SCORING_BY_FORMAT.get(scoring_format.lower(), "PPR")
        payload = await self._get(
            f"/nfl/{season}/consensus-rankings",
            {"position": position, "type": ADP_RANKING_TYPE, "scoring": scoring},
            cache_key=f"adp-{season}-{position}-{scoring}",
        )
        players = payload.get("players")
        if not isinstance(players, list):
            raise ProviderDataError(
                "consensus-rankings payload has no players array",
                provider=SOURCE,
                operation="get_adp",
            )
        return tuple(self._to_rank(row) for row in players if isinstance(row, dict))

    def _to_rank(self, row: dict[str, Any]) -> RankedPlayer:
        """Map one consensus-rankings row.

        `rank_ave` is the mean draft position and `rank_std` its dispersion —
        both present in the real response though absent from the published
        schema. The average is preferred over `rank_ecr`, which is a consensus
        *rank* rather than an average position, and the deviation matters
        because the next-pick survival model is materially better with a real
        one than with its fallback assumption.
        """
        return RankedPlayer(
            provider_player_id=_clean(row.get("player_id")),
            name=_clean(row.get("player_name")),
            team=_clean(row.get("player_team_id")).upper(),
            position=_clean(row.get("player_position_id")).upper(),
            rank=_number(row.get("rank_ave")) or _number(row.get("rank_ecr")),
            rank_stdev=_number(row.get("rank_std")),
            tier=_integer(row.get("tier")),
            bye_week=_integer(row.get("player_bye_week")),
            raw={},
        )
