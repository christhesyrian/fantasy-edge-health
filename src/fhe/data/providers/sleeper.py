"""Sleeper API adapter.

Verified against the official documentation at https://docs.sleeper.com and
against live responses on 2026-08-22. See ``docs/DATA_SOURCES.md`` for the full
record, including the fields the documentation and the live API disagree about.

Key facts, all verified rather than assumed:

* Base URL ``https://api.sleeper.app/v1``, read-only, **no authentication**.
* Documented limit: "stay under 1000 API calls per minute, otherwise you risk
  being IP-blocked". The client self-limits well below that.
* ``/players/nfl`` is roughly 15 MB and the documentation says to call it "once
  per day at most". It is therefore cached on disk and never fetched on a
  request path.

Schema drift already observed and handled:

* ``roster_id`` on a draft pick is documented as a string but arrives as an
  integer. Both are accepted.
* Live pick payloads carry an undocumented ``reactions`` field, which is ignored
  rather than treated as an error.

Not-found behaviour is **inconsistent across endpoints**, verified live:

* Unknown user -> HTTP 200 with a body of ``null``.
* Unknown league or draft -> HTTP 404 with a body of ``null``.

Lookup endpoints therefore treat 404 as "no such resource" and return ``None``,
because failing to find a league during onboarding is a normal outcome rather
than an outage. :meth:`SleeperProvider.get_draft_picks` is the deliberate
exception: it lets a 404 raise, because during live polling an empty list and a
vanished draft must never be confused - one means "nobody has picked yet" and
the other would silently wipe the board.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, Self

import httpx

from fhe.config import Settings
from fhe.data.providers.base import (
    ProviderDataError,
    RateLimiter,
    RetryPolicy,
    classify_http_error,
    with_retries,
)
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME: Final = "sleeper"

# The full player payload is large and slow-changing; the provider documentation
# explicitly asks callers not to fetch it more than once a day.
PLAYER_CACHE_MAX_AGE_SECONDS: Final = 20 * 60 * 60


@dataclass(frozen=True, slots=True)
class SleeperUser:
    """A Sleeper account."""

    user_id: str
    username: str | None
    display_name: str | None
    avatar: str | None = None


@dataclass(frozen=True, slots=True)
class SleeperLeague:
    """A league, with the raw settings preserved for later interpretation."""

    league_id: str
    name: str
    season: str
    total_rosters: int
    status: str
    sport: str
    roster_positions: tuple[str, ...]
    scoring_settings: dict[str, float]
    settings: dict[str, Any]
    draft_id: str | None = None
    previous_league_id: str | None = None
    avatar: str | None = None


@dataclass(frozen=True, slots=True)
class SleeperRoster:
    """One team's roster within a league."""

    roster_id: int
    owner_id: str | None
    league_id: str
    players: tuple[str, ...]
    starters: tuple[str, ...]
    reserve: tuple[str, ...]
    settings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SleeperLeagueUser:
    """A member of a league."""

    user_id: str
    display_name: str | None
    team_name: str | None = None
    avatar: str | None = None


@dataclass(frozen=True, slots=True)
class SleeperDraft:
    """Draft metadata."""

    draft_id: str
    league_id: str | None
    status: str
    draft_type: str
    season: str
    settings: dict[str, Any]
    metadata: dict[str, Any]
    draft_order: dict[str, int]
    slot_to_roster_id: dict[str, int]
    start_time_ms: int | None = None
    last_picked_ms: int | None = None

    @property
    def team_count(self) -> int | None:
        """Number of teams, from draft settings."""
        teams = self.settings.get("teams")
        return int(teams) if isinstance(teams, int | str) and str(teams).isdigit() else None

    @property
    def rounds(self) -> int | None:
        """Number of rounds, from draft settings."""
        rounds = self.settings.get("rounds")
        return int(rounds) if isinstance(rounds, int | str) and str(rounds).isdigit() else None

    @property
    def scoring_type(self) -> str | None:
        """Scoring family declared in draft metadata."""
        value = self.metadata.get("scoring_type")
        return str(value) if value is not None else None


@dataclass(frozen=True, slots=True)
class SleeperPick:
    """A single draft selection."""

    draft_id: str
    pick_no: int
    round_number: int
    draft_slot: int
    player_id: str
    roster_id: int | None
    picked_by: str | None
    is_keeper: bool
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SleeperNflState:
    """Current NFL season state."""

    season: str
    season_type: str
    week: int
    display_week: int | None = None
    previous_season: str | None = None
    season_start_date: str | None = None


@dataclass(frozen=True, slots=True)
class SleeperTrendingPlayer:
    """A player trending in adds or drops."""

    player_id: str
    count: int


def _as_int(value: Any, field: str, operation: str) -> int:
    """Coerce a documented-as-string-but-sometimes-int field."""
    if isinstance(value, bool):
        raise ProviderDataError(
            f"{field} was a boolean", provider=PROVIDER_NAME, operation=operation
        )
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise ProviderDataError(
        f"{field} was not an integer: {value!r}",
        provider=PROVIDER_NAME,
        operation=operation,
    )


def _optional_int(value: Any) -> int | None:
    """Best-effort integer, returning ``None`` rather than raising."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


class SleeperProvider:
    """Typed, resilient async client for the Sleeper API.

    Usage:
        >>> async with SleeperProvider(settings) as sleeper:  # doctest: +SKIP
        ...     state = await sleeper.get_nfl_state()
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._settings = settings
        self._base_url = settings.sleeper_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.sleeper_timeout_seconds),
            headers={"accept": "application/json", "user-agent": "fantasy-health-edge/0.1"},
            follow_redirects=True,
        )
        self._limiter = RateLimiter(settings.sleeper_max_rpm)
        self._retry_policy = retry_policy or RetryPolicy()

    async def __aenter__(self) -> Self:
        """Enter the async context."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the client if this instance created it."""
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying HTTP client."""
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------- core

    async def _get(self, path: str, operation: str, *, allow_missing: bool = False) -> Any:
        """Perform a rate-limited, retried GET returning decoded JSON.

        Args:
            path: Path relative to the API base URL.
            operation: Logical operation name, used for metrics and logs.
            allow_missing: When true, a 404 yields ``None`` instead of raising.
                Set this only for lookups where "not found" is a legitimate
                answer, never for polling a draft that is supposed to exist.
        """

        async def call() -> Any:
            await self._limiter.acquire()
            try:
                response = await self._client.get(f"{self._base_url}{path}")
                if allow_missing and response.status_code == 404:
                    return None
                response.raise_for_status()
            except httpx.HTTPError as error:
                raise classify_http_error(
                    error, provider=PROVIDER_NAME, operation=operation
                ) from error
            try:
                return response.json()
            except json.JSONDecodeError as error:
                raise ProviderDataError(
                    f"{operation} returned invalid JSON",
                    provider=PROVIDER_NAME,
                    operation=operation,
                ) from error

        return await with_retries(operation, PROVIDER_NAME, call, policy=self._retry_policy)

    # ------------------------------------------------------------------ users

    async def get_user(self, username_or_id: str) -> SleeperUser | None:
        """Look up a user by username or user id.

        Returns ``None`` for an unknown user: Sleeper answers with ``null``
        rather than a 404, and "no such user" is a normal onboarding outcome.
        """
        payload = await self._get(f"/user/{username_or_id}", "get_user")
        if not payload:
            return None
        if not isinstance(payload, dict):
            raise ProviderDataError(
                "get_user did not return an object",
                provider=PROVIDER_NAME,
                operation="get_user",
            )
        return SleeperUser(
            user_id=str(payload["user_id"]),
            username=payload.get("username"),
            display_name=payload.get("display_name"),
            avatar=payload.get("avatar"),
        )

    async def get_leagues(self, user_id: str, season: str) -> tuple[SleeperLeague, ...]:
        """All NFL leagues a user belongs to in a season."""
        payload = await self._get(
            f"/user/{user_id}/leagues/nfl/{season}", "get_leagues", allow_missing=True
        )
        if not isinstance(payload, list):
            return ()
        return tuple(self._parse_league(item) for item in payload if isinstance(item, dict))

    async def get_league(self, league_id: str) -> SleeperLeague | None:
        """A single league."""
        payload = await self._get(f"/league/{league_id}", "get_league", allow_missing=True)
        if not payload or not isinstance(payload, dict):
            return None
        return self._parse_league(payload)

    async def get_league_users(self, league_id: str) -> tuple[SleeperLeagueUser, ...]:
        """Members of a league."""
        payload = await self._get(
            f"/league/{league_id}/users", "get_league_users", allow_missing=True
        )
        if not isinstance(payload, list):
            return ()
        return tuple(
            SleeperLeagueUser(
                user_id=str(item["user_id"]),
                display_name=item.get("display_name"),
                team_name=(item.get("metadata") or {}).get("team_name"),
                avatar=item.get("avatar"),
            )
            for item in payload
            if isinstance(item, dict) and item.get("user_id")
        )

    async def get_rosters(self, league_id: str) -> tuple[SleeperRoster, ...]:
        """Rosters in a league."""
        payload = await self._get(f"/league/{league_id}/rosters", "get_rosters", allow_missing=True)
        if not isinstance(payload, list):
            return ()
        rosters: list[SleeperRoster] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            rosters.append(
                SleeperRoster(
                    roster_id=_as_int(item.get("roster_id"), "roster_id", "get_rosters"),
                    owner_id=item.get("owner_id"),
                    league_id=str(item.get("league_id") or league_id),
                    players=tuple(item.get("players") or ()),
                    starters=tuple(item.get("starters") or ()),
                    reserve=tuple(item.get("reserve") or ()),
                    settings=dict(item.get("settings") or {}),
                )
            )
        return tuple(rosters)

    # ----------------------------------------------------------------- drafts

    async def get_league_drafts(self, league_id: str) -> tuple[SleeperDraft, ...]:
        """Every draft belonging to a league."""
        payload = await self._get(
            f"/league/{league_id}/drafts", "get_league_drafts", allow_missing=True
        )
        if not isinstance(payload, list):
            return ()
        return tuple(self._parse_draft(item) for item in payload if isinstance(item, dict))

    async def get_user_drafts(self, user_id: str, season: str) -> tuple[SleeperDraft, ...]:
        """Every draft a user participates in for a season."""
        payload = await self._get(
            f"/user/{user_id}/drafts/nfl/{season}", "get_user_drafts", allow_missing=True
        )
        if not isinstance(payload, list):
            return ()
        return tuple(self._parse_draft(item) for item in payload if isinstance(item, dict))

    async def get_draft(self, draft_id: str) -> SleeperDraft | None:
        """A single draft."""
        payload = await self._get(f"/draft/{draft_id}", "get_draft", allow_missing=True)
        if not payload or not isinstance(payload, dict):
            return None
        return self._parse_draft(payload)

    async def get_draft_picks(self, draft_id: str) -> tuple[SleeperPick, ...]:
        """Every pick made in a draft, in the order the provider returns them.

        The caller is responsible for ordering and de-duplication; that logic
        lives in :class:`fhe.core.draft.state.DraftState` so it is testable
        without a network.

        A 404 deliberately raises rather than returning an empty tuple: "no
        picks yet" and "this draft no longer exists" must stay distinguishable,
        or a transient lookup failure would look like an empty board.
        """
        payload = await self._get(f"/draft/{draft_id}/picks", "get_draft_picks")
        if not isinstance(payload, list):
            return ()

        picks: list[SleeperPick] = []
        for item in payload:
            if not isinstance(item, dict) or not item.get("player_id"):
                continue
            picks.append(
                SleeperPick(
                    draft_id=str(item.get("draft_id") or draft_id),
                    pick_no=_as_int(item.get("pick_no"), "pick_no", "get_draft_picks"),
                    round_number=_as_int(item.get("round"), "round", "get_draft_picks"),
                    draft_slot=_as_int(item.get("draft_slot"), "draft_slot", "get_draft_picks"),
                    player_id=str(item["player_id"]),
                    # Documented as a string, observed as an integer.
                    roster_id=_optional_int(item.get("roster_id")),
                    picked_by=item.get("picked_by") or None,
                    is_keeper=bool(item.get("is_keeper")),
                    metadata=dict(item.get("metadata") or {}),
                )
            )
        return tuple(picks)

    async def get_draft_traded_picks(self, draft_id: str) -> tuple[dict[str, Any], ...]:
        """Traded picks within a draft, returned as raw records.

        Left unmodelled deliberately: traded picks are not consumed by v1's draft
        engine, and inventing a typed shape for data the product does not yet use
        would be speculative.
        """
        payload = await self._get(f"/draft/{draft_id}/traded_picks", "get_draft_traded_picks")
        return tuple(payload) if isinstance(payload, list) else ()

    # ---------------------------------------------------------------- players

    async def get_nfl_state(self) -> SleeperNflState | None:
        """Current NFL season, week, and season type."""
        payload = await self._get("/state/nfl", "get_nfl_state")
        if not payload or not isinstance(payload, dict):
            return None
        return SleeperNflState(
            season=str(payload.get("season", "")),
            season_type=str(payload.get("season_type", "")),
            week=_optional_int(payload.get("week")) or 0,
            display_week=_optional_int(payload.get("display_week")),
            previous_season=payload.get("previous_season"),
            season_start_date=payload.get("season_start_date"),
        )

    async def get_trending_players(
        self,
        trend: Literal["add", "drop"],
        *,
        lookback_hours: int = 24,
        limit: int = 25,
    ) -> tuple[SleeperTrendingPlayer, ...]:
        """Players most added or dropped in the lookback window."""
        payload = await self._get(
            f"/players/nfl/trending/{trend}?lookback_hours={lookback_hours}&limit={limit}",
            "get_trending_players",
        )
        if not isinstance(payload, list):
            return ()
        return tuple(
            SleeperTrendingPlayer(
                player_id=str(item["player_id"]),
                count=_optional_int(item.get("count")) or 0,
            )
            for item in payload
            if isinstance(item, dict) and item.get("player_id")
        )

    async def get_all_players(
        self, *, cache_path: Path | None = None, force_refresh: bool = False
    ) -> dict[str, dict[str, Any]]:
        """The full NFL player universe.

        This is the endpoint the provider asks callers to hit at most once a day,
        so the response is cached on disk and only refetched when the cache is
        older than :data:`PLAYER_CACHE_MAX_AGE_SECONDS`.

        Args:
            cache_path: Where to store the cached payload. Defaults to
                ``<data_dir>/cache/sleeper_players.json``.
            force_refresh: Bypass the cache. Use sparingly and never on a
                request path.
        """
        path = cache_path or (self._settings.data_dir / "cache" / "sleeper_players.json")

        if not force_refresh:
            cached = self._read_player_cache(path)
            if cached is not None:
                return cached

        payload = await self._get("/players/nfl", "get_all_players")
        if not isinstance(payload, dict):
            raise ProviderDataError(
                "get_all_players did not return an object",
                provider=PROVIDER_NAME,
                operation="get_all_players",
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "player_count": len(payload),
            "players": payload,
        }
        # Write via a temporary file so an interrupted write cannot leave a
        # truncated cache that later reads would treat as authoritative.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(envelope))
        temporary.replace(path)

        log.info("sleeper_players_refreshed", player_count=len(payload), path=str(path))
        return payload

    def _read_player_cache(self, path: Path) -> dict[str, dict[str, Any]] | None:
        """Return the cached player payload when it is present and fresh."""
        if not path.exists():
            return None
        age = datetime.now(UTC).timestamp() - path.stat().st_mtime
        if age > PLAYER_CACHE_MAX_AGE_SECONDS:
            log.info("sleeper_player_cache_stale", age_seconds=round(age))
            return None
        try:
            envelope = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as error:
            log.warning("sleeper_player_cache_unreadable", error=str(error))
            return None
        players = envelope.get("players")
        if not isinstance(players, dict):
            log.warning("sleeper_player_cache_malformed")
            return None
        log.info(
            "sleeper_player_cache_hit",
            player_count=len(players),
            age_seconds=round(age),
        )
        return players

    # --------------------------------------------------------------- parsing

    def _parse_league(self, payload: dict[str, Any]) -> SleeperLeague:
        """Build a league from a raw payload."""
        return SleeperLeague(
            league_id=str(payload["league_id"]),
            name=str(payload.get("name", "")),
            season=str(payload.get("season", "")),
            total_rosters=_optional_int(payload.get("total_rosters")) or 0,
            status=str(payload.get("status", "")),
            sport=str(payload.get("sport", "nfl")),
            roster_positions=tuple(payload.get("roster_positions") or ()),
            scoring_settings=dict(payload.get("scoring_settings") or {}),
            settings=dict(payload.get("settings") or {}),
            draft_id=payload.get("draft_id"),
            previous_league_id=payload.get("previous_league_id"),
            avatar=payload.get("avatar"),
        )

    def _parse_draft(self, payload: dict[str, Any]) -> SleeperDraft:
        """Build a draft from a raw payload."""
        raw_order = payload.get("draft_order") or {}
        raw_slots = payload.get("slot_to_roster_id") or {}
        return SleeperDraft(
            draft_id=str(payload["draft_id"]),
            league_id=payload.get("league_id"),
            status=str(payload.get("status", "")),
            draft_type=str(payload.get("type", "")),
            season=str(payload.get("season", "")),
            settings=dict(payload.get("settings") or {}),
            metadata=dict(payload.get("metadata") or {}),
            draft_order={str(k): v for k, v in raw_order.items() if _optional_int(v) is not None},
            slot_to_roster_id={
                str(k): v for k, v in raw_slots.items() if _optional_int(v) is not None
            },
            start_time_ms=_optional_int(payload.get("start_time")),
            last_picked_ms=_optional_int(payload.get("last_picked")),
        )
