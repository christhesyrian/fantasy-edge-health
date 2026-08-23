"""nflverse data adapter.

nflverse publishes open NFL data as versioned assets on GitHub Releases. Verified
on 2026-08-22 by enumerating the releases API and reading the actual files; see
``docs/DATA_SOURCES.md``.

What was verified, not assumed
------------------------------
* Stable asset URL pattern:
  ``https://github.com/nflverse/nflverse-data/releases/download/<tag>/<asset>``
* Injury reports cover **2009 through 2025**. There is no 2026 file yet, which is
  expected: the 2026 season has not started.
* Weekly player stats live under the ``stats_player`` release, not the legacy
  ``player_stats`` one, which stops before the current seasons.
* Snap counts are keyed by ``pfr_player_id``, **not** ``gsis_id``, so they need a
  different crosswalk column from every other dataset here. The 2025 file is complete - 6,068
  rows across weeks 1-22 - and the release was rebuilt on 2026-03-18.
* ``players.parquet`` carries ``gsis_id``, ``espn_id``, ``pfr_id``, ``pff_id``
  and others, but **no** ``sleeper_id``. Linking Sleeper to nflverse therefore
  needs a crosswalk; see :mod:`fhe.data.identity`.
* Practice status contains literal ``"\\n    "`` padding rows that must normalise
  to UNKNOWN rather than being read as a report.

Parquet is preferred over CSV throughout: it is typed, far smaller, and avoids
the string-coercion bugs that come free with CSV.

R is not required. The production path reads the published Parquet assets
directly from Python.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import httpx
import polars as pl

from fhe.config import Settings
from fhe.data.providers.base import (
    ProviderDataError,
    RetryPolicy,
    classify_http_error,
    with_retries,
)
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME: Final = "nflverse"

# Verified coverage. Requesting a season outside this range is a caller bug, not
# a provider failure, and is reported as such instead of producing a confusing
# 404 from GitHub.
INJURY_SEASONS: Final = range(2009, 2026)
SNAP_COUNT_SEASONS: Final = range(2012, 2026)
DEPTH_CHART_SEASONS: Final = range(2001, 2027)
WEEKLY_STATS_SEASONS: Final = range(1999, 2026)

# Downloaded assets are immutable for a given release build, so a long cache is
# safe and keeps the ingestion loop off the network.
ASSET_CACHE_MAX_AGE_SECONDS: Final = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class NflverseAsset:
    """A single downloadable dataset asset."""

    release_tag: str
    filename: str

    @property
    def cache_key(self) -> str:
        """Filesystem-safe cache name."""
        return f"{self.release_tag}__{self.filename}"


class NflverseProvider:
    """Downloads and parses nflverse release assets.

    Args:
        settings: Application settings.
        client: Optional pre-built HTTP client, for tests.
        cache_dir: Where downloaded assets are stored. Defaults to
            ``<data_dir>/cache/nflverse``.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        cache_dir: Path | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.nflverse_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.nflverse_timeout_seconds),
            headers={"user-agent": "fantasy-health-edge/0.1"},
            follow_redirects=True,
        )
        self._cache_dir = cache_dir or (settings.data_dir / "cache" / "nflverse")
        self._retry_policy = retry_policy or RetryPolicy()

    async def __aenter__(self) -> NflverseProvider:
        """Enter the async context."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the client if this instance created it."""
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------- downloads

    async def fetch_asset(self, asset: NflverseAsset, *, force_refresh: bool = False) -> bytes:
        """Download an asset, using the on-disk cache when it is fresh."""
        cache_path = self._cache_dir / asset.cache_key
        if not force_refresh and self._cache_is_fresh(cache_path):
            log.info("nflverse_cache_hit", asset=asset.filename)
            return cache_path.read_bytes()

        url = f"{self._base_url}/{asset.release_tag}/{asset.filename}"
        operation = f"fetch_{asset.release_tag}"

        async def call() -> bytes:
            try:
                response = await self._client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise classify_http_error(
                    error, provider=PROVIDER_NAME, operation=operation
                ) from error
            return response.content

        content = await with_retries(operation, PROVIDER_NAME, call, policy=self._retry_policy)

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(cache_path)

        log.info("nflverse_asset_downloaded", asset=asset.filename, bytes=len(content))
        return content

    def _cache_is_fresh(self, path: Path) -> bool:
        """Whether a cached asset exists and is within its max age."""
        if not path.exists():
            return False
        age = datetime.now(UTC).timestamp() - path.stat().st_mtime
        return age <= ASSET_CACHE_MAX_AGE_SECONDS

    async def _read_parquet(
        self, asset: NflverseAsset, *, force_refresh: bool = False
    ) -> pl.DataFrame:
        """Fetch an asset and parse it as Parquet."""
        content = await self.fetch_asset(asset, force_refresh=force_refresh)
        try:
            # Parsing is CPU-bound; keep it off the event loop.
            return await asyncio.to_thread(pl.read_parquet, io.BytesIO(content))
        except (pl.exceptions.PolarsError, ValueError, OSError) as error:
            raise ProviderDataError(
                f"could not parse {asset.filename} as Parquet: {error}",
                provider=PROVIDER_NAME,
                operation=f"parse_{asset.release_tag}",
            ) from error

    @staticmethod
    def _require_season(season: int, allowed: range, dataset: str) -> None:
        """Fail fast with a useful message for an out-of-range season."""
        if season not in allowed:
            raise ProviderDataError(
                f"{dataset} is published for {allowed.start}-{allowed.stop - 1}; "
                f"{season} is not available",
                provider=PROVIDER_NAME,
                operation=f"fetch_{dataset}",
            )

    # -------------------------------------------------------------- datasets

    async def get_players(self, *, force_refresh: bool = False) -> pl.DataFrame:
        """The nflverse player table: identity, position, and external ids.

        Note this table has no ``sleeper_id``; see :mod:`fhe.data.identity`.
        """
        return await self._read_parquet(
            NflverseAsset("players", "players.parquet"), force_refresh=force_refresh
        )

    async def get_injuries(self, season: int, *, force_refresh: bool = False) -> pl.DataFrame:
        """Weekly injury reports for a season.

        Columns (verified): ``season``, ``game_type``, ``team``, ``week``,
        ``gsis_id``, ``position``, ``full_name``, ``first_name``, ``last_name``,
        ``report_primary_injury``, ``report_secondary_injury``, ``report_status``,
        ``practice_primary_injury``, ``practice_secondary_injury``,
        ``practice_status``, ``date_modified``.
        """
        self._require_season(season, INJURY_SEASONS, "injuries")
        return await self._read_parquet(
            NflverseAsset("injuries", f"injuries_{season}.parquet"),
            force_refresh=force_refresh,
        )

    async def get_snap_counts(self, season: int, *, force_refresh: bool = False) -> pl.DataFrame:
        """Per-game snap counts for a season."""
        self._require_season(season, SNAP_COUNT_SEASONS, "snap_counts")
        return await self._read_parquet(
            NflverseAsset("snap_counts", f"snap_counts_{season}.parquet"),
            force_refresh=force_refresh,
        )

    async def get_depth_charts(self, season: int, *, force_refresh: bool = False) -> pl.DataFrame:
        """Weekly depth charts for a season."""
        self._require_season(season, DEPTH_CHART_SEASONS, "depth_charts")
        return await self._read_parquet(
            NflverseAsset("depth_charts", f"depth_charts_{season}.parquet"),
            force_refresh=force_refresh,
        )

    async def get_weekly_player_stats(
        self, season: int, *, force_refresh: bool = False
    ) -> pl.DataFrame:
        """Weekly player statistics for a season.

        Published under the ``stats_player`` release as
        ``stats_player_week_{season}.parquet``. The older ``player_stats`` tag
        still exists but stops before the current seasons - requesting 2025
        there returns a 404 - so this deliberately reads the maintained one.

        Keyed by ``player_id``, which holds a ``gsis_id`` despite the name.
        """
        self._require_season(season, WEEKLY_STATS_SEASONS, "player_stats")
        return await self._read_parquet(
            NflverseAsset("stats_player", f"stats_player_week_{season}.parquet"),
            force_refresh=force_refresh,
        )

    async def get_rosters(self, season: int, *, force_refresh: bool = False) -> pl.DataFrame:
        """Season rosters, which carry age, experience, and jersey number."""
        return await self._read_parquet(
            NflverseAsset("rosters", f"roster_{season}.parquet"),
            force_refresh=force_refresh,
        )
