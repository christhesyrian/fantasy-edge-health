"""Sync the Sleeper player universe into ``players`` and friends.

This is the job that establishes the player table every other dataset joins
against, so identity resolution happens here and nowhere else.

What it writes:

* ``players`` - one row per resolved player, carrying the resolution method and
  confidence so a downstream consumer can tell a certain match from a guess.
* ``player_external_ids`` - every provider identifier discovered, as rows.
* ``current_player_health`` - the live injury designation and depth-chart
  position from Sleeper.
* ``player_identity_conflicts`` - players that could not be resolved. These are
  recorded, never dropped and never force-matched.

Three safety properties matter here:

* **An empty or tiny payload aborts the sync.** Sleeper returning 40 players
  instead of 12,000 is a provider incident, and letting it through would mark
  almost the entire league inactive.
* **Everything is an upsert.** Re-running converges rather than duplicating, and
  a player's ``player_uuid`` is derived deterministically so it never changes.
* **External ids are de-duplicated before writing.** The upstream crosswalk
  contains a small number of genuine errors where one provider id is claimed by
  two different players - measured at 3 collisions across 24,441 id pairs, such
  as a ``stats_id`` shared by two unrelated players. The schema is right to
  forbid that, so the collision is resolved deterministically and the loser is
  reported as a rejection instead of crashing the sync.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.core.injury import normalize_body_region, normalize_designation
from fhe.core.types import BodyRegion, Position
from fhe.data.identity import (
    IdentityConflict,
    IdentityResolver,
    NflversePlayerIndex,
    PlayerCrosswalk,
    ResolvedIdentity,
    clean_token,
    normalize_name,
)
from fhe.data.ingest.run import IngestionRunRecorder, ingestion_run
from fhe.db.base import utcnow
from fhe.db.models.health import CurrentPlayerHealth
from fhe.db.models.player import Player, PlayerExternalId, PlayerIdentityConflict
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger

log = get_logger(__name__)

PROVIDER_NAME: Final = "sleeper"
DATASET: Final = "players"

# Below this the payload is treated as a provider incident rather than data.
# The live endpoint returns ~12,000 players; a tenth of that is not a roster
# change, it is a broken response.
MIN_PLAUSIBLE_PLAYER_COUNT: Final = 2000

# Positions worth storing. The rest of the league (linemen, special teamers) is
# ~9,000 rows of noise for a fantasy product, and excluding them keeps the
# player table small enough to hold in memory during a draft.
FANTASY_POSITIONS: Final[frozenset[Position]] = frozenset(
    {Position.QB, Position.RB, Position.WR, Position.TE, Position.K, Position.DEF}
)


class SleeperPlayerSource(Protocol):
    """The only capability this job needs from a Sleeper client.

    Depending on the narrow protocol rather than the concrete provider keeps the
    job honest about its requirements and lets it be driven from a fixture
    without a network stub pretending to be a full API client.
    """

    async def get_all_players(
        self, *, cache_path: Path | None = ..., force_refresh: bool = ...
    ) -> dict[str, Any]:
        """Return the full player universe keyed by provider player id."""
        ...


class PlayerSyncAbortedError(RuntimeError):
    """The payload was too small to be a legitimate player universe."""


@dataclass(frozen=True, slots=True)
class _IdClaim:
    """One player's claim on a provider identifier."""

    player_uuid: str
    confidence: float
    sleeper_id: str
    name: str


def _resolve_id_claims(
    claims: dict[tuple[str, str], list[_IdClaim]], run: IngestionRunRecorder
) -> list[dict[str, Any]]:
    """Pick one owner per external id, reporting anyone who loses the claim.

    An external identifier must identify exactly one player - that is what makes
    it useful as a join key - so a contested id is a real upstream data error,
    not something to absorb quietly.

    The winner is the claim with the highest identity confidence, breaking ties
    on the Sleeper id so the outcome is deterministic across runs rather than
    dependent on dictionary iteration order.
    """
    rows: list[dict[str, Any]] = []
    for (system, external_id), candidates in claims.items():
        distinct = {c.player_uuid for c in candidates}
        if len(distinct) > 1:
            ranked = sorted(candidates, key=lambda c: (-c.confidence, c.sleeper_id))
            winner, *losers = ranked
            for loser in losers:
                if loser.player_uuid == winner.player_uuid:
                    continue
                run.reject(
                    "external_id_claimed_by_another_player",
                    system=system,
                    external_id=external_id,
                    kept_player=winner.name,
                    kept_sleeper_id=winner.sleeper_id,
                    dropped_player=loser.name,
                    dropped_sleeper_id=loser.sleeper_id,
                )
        else:
            winner = candidates[0]
        rows.append(
            {
                "player_uuid": winner.player_uuid,
                "system": system,
                "external_id": external_id,
            }
        )
    return rows


def _parse_height_inches(raw: Any) -> int | None:
    """Sleeper reports height either as inches ("69") or as feet-inches ("5'9")."""
    text = clean_token(raw)
    if text is None:
        return None
    if text.isdigit():
        value = int(text)
        return value if 50 <= value <= 90 else None
    if "'" in text:
        feet, _, inches = text.partition("'")
        if feet.strip().isdigit():
            total = int(feet.strip()) * 12 + int(inches.strip('" ') or 0)
            return total if 50 <= total <= 90 else None
    return None


def _parse_int(raw: Any, *, low: int, high: int) -> int | None:
    """Parse a bounded integer, rejecting implausible values."""
    text = clean_token(raw)
    if text is None:
        return None
    try:
        value = int(float(text))
    except (TypeError, ValueError):
        return None
    return value if low <= value <= high else None


def _parse_float(raw: Any, *, low: float, high: float) -> float | None:
    """Parse a bounded float, rejecting implausible values."""
    text = clean_token(raw)
    if text is None:
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if low <= value <= high else None


def _display_name(raw: dict[str, Any]) -> str:
    """Best available name for a Sleeper record."""
    full = clean_token(raw.get("full_name"))
    if full:
        return full
    parts = [clean_token(raw.get("first_name")), clean_token(raw.get("last_name"))]
    return " ".join(p for p in parts if p) or "Unknown Player"


def _player_row(
    raw: dict[str, Any], identity: ResolvedIdentity, name: str, position: Position
) -> dict[str, Any]:
    """Build the ``players`` row for a resolved player."""
    now = utcnow()
    return {
        "player_uuid": identity.player_uuid,
        "full_name": name,
        "normalized_name": normalize_name(name),
        "first_name": clean_token(raw.get("first_name")),
        "last_name": clean_token(raw.get("last_name")),
        "position": position.value,
        "team": clean_token(raw.get("team")),
        "jersey_number": _parse_int(raw.get("number"), low=0, high=99),
        "birth_date": None,
        # Bounds reject the impossible ages that appear in provider feeds.
        "age": _parse_float(raw.get("age"), low=17.0, high=50.0),
        "years_experience": _parse_int(raw.get("years_exp"), low=0, high=30),
        "height_inches": _parse_height_inches(raw.get("height")),
        "weight_pounds": _parse_int(raw.get("weight"), low=120, high=400),
        "college": clean_token(raw.get("college")),
        "is_active": bool(raw.get("active")),
        "popularity_rank": _parse_int(raw.get("search_rank"), low=0, high=1_000_000),
        "identity_method": identity.method.value,
        "identity_confidence": identity.confidence,
        "source": PROVIDER_NAME,
        "ingested_at": now,
        "observed_at": now,
        "source_updated_at": None,
    }


def _health_row(raw: dict[str, Any], identity: ResolvedIdentity) -> dict[str, Any] | None:
    """Build a ``current_player_health`` row, or ``None`` when nothing is reported.

    Only players with something to say get a row. Writing a "healthy" record for
    every player would assert something the source never claimed - absence of a
    report is not evidence of health.
    """
    raw_status = clean_token(raw.get("injury_status"))
    raw_body_part = clean_token(raw.get("injury_body_part"))
    depth_position = clean_token(raw.get("depth_chart_position"))
    depth_order = _parse_int(raw.get("depth_chart_order"), low=0, high=30)

    if not any((raw_status, raw_body_part, depth_position, depth_order is not None)):
        return None

    region = normalize_body_region(raw_body_part) if raw_body_part else None
    now = utcnow()
    return {
        "player_uuid": identity.player_uuid,
        "designation": normalize_designation(raw_status).value,
        "raw_injury_status": raw_status,
        "body_region": region.value if region and region is not BodyRegion.OTHER_UNKNOWN else None,
        "raw_body_part": raw_body_part,
        "injury_notes": clean_token(raw.get("injury_notes")),
        "injury_start_date": None,
        # Sleeper's practice fields are populated for a single player out of
        # ~12,000, so they are deliberately not read here. Practice data comes
        # from nflverse; see docs/DATA_SOURCES.md.
        "practice_status": None,
        "practice_trajectory": None,
        "depth_chart_position": depth_position,
        "depth_chart_order": depth_order,
        "source": PROVIDER_NAME,
        "ingested_at": now,
        "observed_at": now,
        "source_updated_at": None,
    }


def _conflict_row(conflict: IdentityConflict) -> dict[str, Any]:
    """Build a ``player_identity_conflicts`` row."""
    return {
        "sleeper_id": conflict.sleeper_id,
        "observed_name": conflict.name,
        "normalized_name": conflict.normalized_name,
        "team": conflict.team,
        "position": conflict.position.value,
        "reason": conflict.reason.value,
        "candidate_ids": json.dumps(list(conflict.candidate_gsis_ids)),
        "detail": conflict.detail,
        "resolved": False,
        "resolved_player_uuid": None,
    }


async def sync_sleeper_players(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    sleeper: SleeperPlayerSource,
    crosswalk: PlayerCrosswalk | None = None,
    nflverse_index: NflversePlayerIndex | None = None,
    force_refresh: bool = False,
    positions: frozenset[Position] = FANTASY_POSITIONS,
    min_player_count: int = MIN_PLAUSIBLE_PLAYER_COUNT,
) -> IngestionRunRecorder:
    """Fetch the Sleeper player universe and persist the fantasy-relevant slice.

    Args:
        session_factory: Async session factory.
        sleeper: Provider client.
        crosswalk: External id crosswalk. Strongly recommended - without it
            nflverse linkage drops from ~97% to ~21%.
        nflverse_index: Index for name-based fallback matching.
        force_refresh: Bypass the on-disk player cache.
        positions: Positions to persist.
        min_player_count: Plausibility floor below which the payload is treated
            as a provider incident. Exposed so a focused test can drive the job
            with a handful of records; production callers must leave it at the
            default, which is why that default is the safe value rather than 0.

    Returns:
        The run recorder, with counts of what happened.

    Raises:
        PlayerSyncAbortedError: If the payload is implausibly small.
    """
    async with ingestion_run(
        session_factory,
        provider=PROVIDER_NAME,
        dataset=DATASET,
        requested_resource="/players/nfl",
    ) as run:
        payload = await sleeper.get_all_players(force_refresh=force_refresh)
        run.read(len(payload))

        if len(payload) < min_player_count:
            # Refuse rather than degrade: writing this would mark most of the
            # league inactive and quietly poison every downstream join.
            raise PlayerSyncAbortedError(
                f"Sleeper returned {len(payload)} players, below the "
                f"{min_player_count} plausibility floor; refusing to "
                "overwrite known-good player state"
            )

        resolver = IdentityResolver(crosswalk=crosswalk, nflverse_index=nflverse_index)

        player_rows: list[dict[str, Any]] = []
        health_rows: list[dict[str, Any]] = []
        conflict_rows: list[dict[str, Any]] = []
        seen_uuids: set[str] = set()
        # (system, external_id) -> candidate claims, resolved after the loop so
        # the winner does not depend on payload iteration order.
        id_claims: dict[tuple[str, str], list[_IdClaim]] = {}

        for raw in payload.values():
            if not isinstance(raw, dict):
                run.reject("payload_entry_not_an_object")
                continue

            sleeper_id = clean_token(raw.get("player_id"))
            if sleeper_id is None:
                run.reject("missing_player_id", name=_display_name(raw))
                continue

            position = Position.parse(clean_token(raw.get("position")))
            if position not in positions:
                continue  # filtered, not rejected: this is a deliberate scope choice

            name = _display_name(raw)
            outcome = resolver.resolve(
                sleeper_id=sleeper_id,
                name=name,
                position=position,
                team=clean_token(raw.get("team")),
                gsis_id=clean_token(raw.get("gsis_id")),
            )

            if isinstance(outcome, IdentityConflict):
                conflict_rows.append(_conflict_row(outcome))
                run.unresolved_identity()
                continue

            if outcome.player_uuid in seen_uuids:
                # Two Sleeper records resolving to one player is a real signal
                # worth recording rather than an upsert to shrug at.
                run.reject(
                    "duplicate_player_uuid",
                    sleeper_id=sleeper_id,
                    name=name,
                    player_uuid=outcome.player_uuid,
                )
                continue
            seen_uuids.add(outcome.player_uuid)

            player_rows.append(_player_row(raw, outcome, name, position))

            for system, value in outcome.external_ids.items():
                id_claims.setdefault((system, value), []).append(
                    _IdClaim(
                        player_uuid=outcome.player_uuid,
                        confidence=outcome.confidence,
                        sleeper_id=sleeper_id,
                        name=name,
                    )
                )

            health = _health_row(raw, outcome)
            if health is not None:
                health_rows.append(health)

        external_rows = _resolve_id_claims(id_claims, run)

        async with session_factory() as session:
            written = await upsert_rows(
                session,
                Player,
                player_rows,
                conflict_columns=["player_uuid"],
            )
            await upsert_rows(
                session,
                PlayerExternalId,
                external_rows,
                conflict_columns=["player_uuid", "system"],
                update_columns=["external_id"],
            )
            await upsert_rows(
                session,
                CurrentPlayerHealth,
                health_rows,
                conflict_columns=["player_uuid"],
            )
            await upsert_rows(
                session,
                PlayerIdentityConflict,
                conflict_rows,
                conflict_columns=["sleeper_id", "reason"],
                update_columns=["observed_name", "team", "position", "candidate_ids", "detail"],
            )
            await session.commit()

        run.wrote(written)
        run.details.update(
            {
                "players_in_payload": len(payload),
                "players_persisted": len(player_rows),
                "external_ids_written": len(external_rows),
                "health_rows_written": len(health_rows),
                "identity_conflicts": len(conflict_rows),
                "crosswalk_available": crosswalk is not None,
                "nflverse_index_available": nflverse_index is not None,
                "anchored_to_nflverse": sum(
                    1 for r in player_rows if r["identity_method"] != "UNRESOLVED"
                ),
            }
        )

    return run
