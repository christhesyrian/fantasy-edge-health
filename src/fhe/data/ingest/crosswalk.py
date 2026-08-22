"""Fetch the external player-id crosswalk.

The DynastyProcess ``db_playerids.csv`` dataset bridges Sleeper ids to
``gsis_id``, which is what makes nflverse history reachable at all: Sleeper
publishes ``gsis_id`` for only about a fifth of fantasy-relevant players, and
nflverse publishes no Sleeper id.

**Licensing.** That dataset is GPL-3.0 while this project is MIT, so it is
downloaded at runtime into a git-ignored cache and never redistributed as part
of this repository. The resolver degrades gracefully when it is absent - it just
links far fewer players, and says so.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import httpx

from fhe.config import Settings
from fhe.data.identity import PlayerCrosswalk
from fhe.data.providers.base import (
    ProviderError,
    RetryPolicy,
    classify_http_error,
    with_retries,
)
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME = "dynastyprocess"
CACHE_FILENAME = "db_playerids.csv"
# The upstream repository regenerates the file weekly, so a day is comfortably
# fresh while keeping ingestion off the network on repeat runs.
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60


async def fetch_crosswalk_csv(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    cache_path: Path | None = None,
    force_refresh: bool = False,
) -> str:
    """Download the crosswalk CSV, using the cache when it is fresh.

    Returns:
        The raw CSV text.
    """
    path = cache_path or (settings.data_dir / "cache" / CACHE_FILENAME)

    if not force_refresh and path.exists():
        age = datetime.now(UTC).timestamp() - path.stat().st_mtime
        if age <= CACHE_MAX_AGE_SECONDS:
            log.info("crosswalk_cache_hit", age_seconds=round(age))
            return path.read_text(encoding="utf-8")

    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),
        headers={"user-agent": "fantasy-health-edge/0.1"},
        follow_redirects=True,
    )

    async def call() -> str:
        try:
            response = await http.get(settings.crosswalk_url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise classify_http_error(
                error, provider=PROVIDER_NAME, operation="fetch_crosswalk"
            ) from error
        return response.text

    try:
        text = await with_retries("fetch_crosswalk", PROVIDER_NAME, call, policy=RetryPolicy())
    finally:
        if owns_client:
            await http.aclose()

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)

    log.info("crosswalk_downloaded", bytes=len(text), path=str(path))
    return text


async def load_crosswalk(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    cache_path: Path | None = None,
    force_refresh: bool = False,
    required: bool = False,
) -> PlayerCrosswalk | None:
    """Load the crosswalk, returning ``None`` when it cannot be obtained.

    Args:
        required: When true, a failure raises instead of degrading. Ingestion
            leaves this false, because linking fewer players is much better than
            refusing to ingest at all.
    """
    try:
        text = await fetch_crosswalk_csv(
            settings, client=client, cache_path=cache_path, force_refresh=force_refresh
        )
    except (ProviderError, OSError) as error:
        # Narrow on purpose: a provider failure or an unwritable cache are the
        # two things that legitimately degrade. Anything else is a bug here and
        # should surface rather than be absorbed as "crosswalk unavailable".
        if required:
            raise
        log.warning(
            "crosswalk_unavailable",
            error=str(error),
            impact="players will link to nflverse history at a much lower rate",
        )
        return None

    return PlayerCrosswalk.from_rows(csv.DictReader(StringIO(text)))
