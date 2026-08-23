"""Player identity resolution.

The hardest data problem in this system. Sleeper, nflverse, and every projection
source name the same human differently, and none of them shares a primary key.

Measured reality (2026-08-22, over the live Sleeper payload and nflverse
``players.parquet``):

* Sleeper's own ``gsis_id`` is present for only **~21%** of the top 200
  fantasy-relevant players. Joining on it alone strands four fifths of the pool.
* ``sportradar_id`` is present for ~100% of Sleeper players, but nflverse does
  not publish it, so it cannot bridge the two.
* Adding the DynastyProcess crosswalk lifts Sleeper -> nflverse resolution to
  **94.5%** of the top 200 and 88.8% of the top 400.

So the resolver is deliberately layered, strongest evidence first, and anything
it cannot resolve deterministically is recorded for a human rather than guessed.

Internal identity
-----------------
Each player gets an immutable ``player_uuid`` derived deterministically (UUIDv5)
from the strongest identifier available. The same input always produces the same
UUID, so re-running ingestion is idempotent and the key is stable across
rebuilds, without needing a database round-trip to allocate it.
"""

from __future__ import annotations

import csv
import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum, unique
from pathlib import Path
from typing import Any, Final

from fhe.core.types import Position
from fhe.observability import get_logger

log = get_logger(__name__)

# Fixed namespace for deterministic player UUIDs. Changing this value would
# re-key every player in the database, so it is a constant, never configuration.
FHE_PLAYER_NAMESPACE: Final = uuid.UUID("6f1b9c1e-6f9a-5d4c-9a1e-3f0d5b7c2a10")

# R writes missing values as the literal string "NA". Treating that as data is a
# genuine trap: it silently creates one enormous player whose id is "NA".
NULL_TOKENS: Final[frozenset[str]] = frozenset({"", "na", "n/a", "null", "none", "nan"})

_NAME_SUFFIXES: Final[frozenset[str]] = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_NON_ALPHA: Final = re.compile(r"[^a-z\s]")
_WHITESPACE: Final = re.compile(r"\s+")

# Confidence assigned to each resolution route.
_CONFIDENCE: Final[dict[str, float]] = {
    "DIRECT_GSIS": 1.0,
    "CROSSWALK": 0.97,
    "NAME_TEAM_POSITION": 0.85,
    "NAME_POSITION_DOB": 0.90,
    "NAME_POSITION": 0.60,
}
# Below this, a match is not applied; it becomes a conflict for review.
MIN_AUTO_ACCEPT_CONFIDENCE: Final = 0.80


@unique
class ResolutionMethod(StrEnum):
    """How an identity was established, strongest first."""

    DIRECT_GSIS = "DIRECT_GSIS"
    CROSSWALK = "CROSSWALK"
    NAME_POSITION_DOB = "NAME_POSITION_DOB"
    NAME_TEAM_POSITION = "NAME_TEAM_POSITION"
    NAME_POSITION = "NAME_POSITION"
    UNRESOLVED = "UNRESOLVED"


@unique
class ConflictReason(StrEnum):
    """Why a player could not be resolved automatically."""

    NO_CANDIDATE = "NO_CANDIDATE"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """A player mapped onto the internal identity space."""

    player_uuid: str
    method: ResolutionMethod
    confidence: float
    sleeper_id: str | None = None
    gsis_id: str | None = None
    external_ids: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_anchored_to_nflverse(self) -> bool:
        """Whether this player can join to nflverse historical data."""
        return self.gsis_id is not None


@dataclass(frozen=True, slots=True)
class IdentityConflict:
    """An unresolved player, recorded with enough context to investigate.

    These are written to ``player_identity_conflicts`` rather than being silently
    dropped or force-matched. A player the system cannot identify is a known
    unknown, not an invisible one.
    """

    sleeper_id: str | None
    name: str
    normalized_name: str
    team: str | None
    position: Position
    reason: ConflictReason
    candidate_gsis_ids: tuple[str, ...] = field(default=())
    detail: str = ""


def clean_token(value: Any) -> str | None:
    """Return a trimmed string, or ``None`` for any null-ish token.

    Handles the ``"NA"`` sentinel that R-generated CSVs use for missing values.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in NULL_TOKENS:
        return None
    return text


def normalize_name(name: str | None) -> str:
    """Reduce a player name to a comparable key.

    Strips accents, punctuation, and generational suffixes, then collapses
    whitespace. ``"D.K. Metcalf"``, ``"DK Metcalf"`` and ``"Dk  Metcalf"`` all
    converge; ``"Odell Beckham Jr."`` and ``"Odell Beckham"`` do too.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    lowered = _NON_ALPHA.sub(" ", ascii_only.lower())
    parts = [p for p in _WHITESPACE.sub(" ", lowered).strip().split(" ") if p]
    while len(parts) > 1 and parts[-1] in _NAME_SUFFIXES:
        parts.pop()
    return "".join(parts)


def make_player_uuid(*, gsis_id: str | None, sleeper_id: str | None) -> str:
    """Derive a stable internal identifier.

    Anchored on ``gsis_id`` when available, because that is the identifier the
    historical data is keyed by and it never changes. Falls back to the Sleeper
    id so a player with no nflverse presence - an undrafted rookie in August -
    still gets a durable key.

    Raises:
        ValueError: If neither identifier is supplied. A player with no
            identifier at all must become a conflict, not a random UUID.
    """
    anchor_gsis = clean_token(gsis_id)
    anchor_sleeper = clean_token(sleeper_id)
    if anchor_gsis:
        return str(uuid.uuid5(FHE_PLAYER_NAMESPACE, f"gsis:{anchor_gsis}"))
    if anchor_sleeper:
        return str(uuid.uuid5(FHE_PLAYER_NAMESPACE, f"sleeper:{anchor_sleeper}"))
    raise ValueError("cannot mint a player_uuid without a gsis_id or sleeper_id")


@dataclass(frozen=True, slots=True)
class CrosswalkEntry:
    """One row of the external id crosswalk."""

    sleeper_id: str
    gsis_id: str | None
    external_ids: Mapping[str, str]
    name: str | None = None
    position: str | None = None
    team: str | None = None


class PlayerCrosswalk:
    """Sleeper-id-keyed lookup of external identifiers.

    Built from the DynastyProcess ``db_playerids.csv`` dataset, which is the
    community-maintained bridge between fantasy platform ids and ``gsis_id``.
    Missing values in that file are the literal string ``"NA"``; every read goes
    through :func:`clean_token` so that never becomes data.
    """

    ID_COLUMNS: Final[tuple[str, ...]] = (
        "gsis_id",
        "espn_id",
        "yahoo_id",
        "fantasypros_id",
        "pfr_id",
        "sportradar_id",
        "fantasy_data_id",
        "rotowire_id",
        "cbs_id",
        "mfl_id",
        "pff_id",
        "stats_id",
        "ktc_id",
    )

    def __init__(self, entries: Iterable[CrosswalkEntry]) -> None:
        self._by_sleeper: dict[str, CrosswalkEntry] = {}
        self._by_gsis: dict[str, CrosswalkEntry] = {}
        for entry in entries:
            self._by_sleeper[entry.sleeper_id] = entry
            if entry.gsis_id:
                self._by_gsis[entry.gsis_id] = entry

    def __len__(self) -> int:
        """Number of Sleeper-keyed entries."""
        return len(self._by_sleeper)

    @property
    def gsis_coverage(self) -> int:
        """How many entries carry a usable ``gsis_id``."""
        return len(self._by_gsis)

    def by_sleeper_id(self, sleeper_id: str) -> CrosswalkEntry | None:
        """Look up an entry by Sleeper player id."""
        return self._by_sleeper.get(str(sleeper_id))

    @classmethod
    def from_csv(cls, path: Path) -> PlayerCrosswalk:
        """Load the crosswalk from a CSV file."""
        with path.open(newline="", encoding="utf-8") as handle:
            return cls.from_rows(csv.DictReader(handle))

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]]) -> PlayerCrosswalk:
        """Build a crosswalk from already-parsed rows."""
        entries: list[CrosswalkEntry] = []
        skipped = 0
        for row in rows:
            sleeper_id = clean_token(row.get("sleeper_id"))
            if sleeper_id is None:
                skipped += 1
                continue
            external = {
                column: value
                for column in cls.ID_COLUMNS
                if (value := clean_token(row.get(column))) is not None
            }
            entries.append(
                CrosswalkEntry(
                    sleeper_id=sleeper_id,
                    gsis_id=external.get("gsis_id"),
                    external_ids=external,
                    name=clean_token(row.get("name")),
                    position=clean_token(row.get("position")),
                    team=clean_token(row.get("team")),
                )
            )
        crosswalk = cls(entries)
        log.info(
            "crosswalk_loaded",
            entries=len(crosswalk),
            with_gsis=crosswalk.gsis_coverage,
            rows_without_sleeper_id=skipped,
        )
        return crosswalk


# Identifiers nflverse publishes on its player table. Harvested because the
# community crosswalk only covers players it has a Sleeper id for, while
# nflverse covers anyone who has ever appeared - which is what makes snap
# counts, the one dataset keyed on pfr_id, joinable for the rest.
NFLVERSE_ID_COLUMNS: Final[tuple[str, ...]] = (
    "pfr_id",
    "espn_id",
    "pff_id",
    "otc_id",
    "esb_id",
    "nfl_id",
    "smart_id",
)


@dataclass(frozen=True, slots=True)
class NflversePlayerIndex:
    """Indexes over nflverse players: identifiers, and names for fallback."""

    by_gsis: Mapping[str, Mapping[str, Any]]
    by_name_team_position: Mapping[tuple[str, str, str], tuple[str, ...]]
    by_name_position: Mapping[tuple[str, str], tuple[str, ...]]
    external_ids_by_gsis: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @classmethod
    def build(cls, players: Iterable[Mapping[str, Any]]) -> NflversePlayerIndex:
        """Index nflverse player rows for lookup."""
        by_gsis: dict[str, Mapping[str, Any]] = {}
        external: dict[str, Mapping[str, str]] = {}
        name_team_pos: dict[tuple[str, str, str], list[str]] = {}
        name_pos: dict[tuple[str, str], list[str]] = {}

        for row in players:
            gsis = clean_token(row.get("gsis_id"))
            if gsis is None:
                continue
            by_gsis[gsis] = row

            ids = {
                column: value
                for column in NFLVERSE_ID_COLUMNS
                if (value := clean_token(row.get(column))) is not None
            }
            if ids:
                external[gsis] = ids

            name_key = normalize_name(
                clean_token(row.get("display_name")) or clean_token(row.get("full_name"))
            )
            if not name_key:
                continue
            position = (clean_token(row.get("position")) or "").upper()
            team = (clean_token(row.get("latest_team")) or "").upper()

            if position:
                name_pos.setdefault((name_key, position), []).append(gsis)
                if team:
                    name_team_pos.setdefault((name_key, team, position), []).append(gsis)

        return cls(
            by_gsis=by_gsis,
            by_name_team_position={k: tuple(v) for k, v in name_team_pos.items()},
            by_name_position={k: tuple(v) for k, v in name_pos.items()},
            external_ids_by_gsis=external,
        )


@dataclass(frozen=True, slots=True)
class ResolutionReport:
    """Aggregate outcome of resolving a batch of players."""

    resolved: tuple[ResolvedIdentity, ...]
    conflicts: tuple[IdentityConflict, ...]
    method_counts: Mapping[ResolutionMethod, int]

    @property
    def total(self) -> int:
        """How many players were considered."""
        return len(self.resolved) + len(self.conflicts)

    @property
    def anchored_rate(self) -> float:
        """Share of players linked through to nflverse history."""
        if not self.total:
            return 0.0
        return sum(1 for r in self.resolved if r.is_anchored_to_nflverse) / self.total


class IdentityResolver:
    """Resolves Sleeper players onto internal identities.

    Args:
        crosswalk: External id crosswalk. Optional - without it the resolver
            still works, just with far lower nflverse linkage.
        nflverse_index: Index of nflverse players, for name-based fallback.
    """

    def __init__(
        self,
        *,
        crosswalk: PlayerCrosswalk | None = None,
        nflverse_index: NflversePlayerIndex | None = None,
    ) -> None:
        self._crosswalk = crosswalk
        self._index = nflverse_index

    def resolve(
        self,
        *,
        sleeper_id: str,
        name: str,
        position: Position,
        team: str | None = None,
        gsis_id: str | None = None,
    ) -> ResolvedIdentity | IdentityConflict:
        """Resolve one player, strongest evidence first.

        Returns either a resolved identity or a conflict record. It never
        returns a low-confidence match silently.
        """
        normalized = normalize_name(name)
        external: dict[str, str] = {}

        # 1. The Sleeper payload already carried a gsis_id. Nothing beats that.
        direct = clean_token(gsis_id)
        if direct:
            return self._build(sleeper_id, direct, ResolutionMethod.DIRECT_GSIS, external)

        # 2. The crosswalk, which is what lifts coverage from ~21% to ~95%.
        if self._crosswalk is not None:
            entry = self._crosswalk.by_sleeper_id(sleeper_id)
            if entry is not None:
                external.update(entry.external_ids)
                if entry.gsis_id:
                    return self._build(
                        sleeper_id, entry.gsis_id, ResolutionMethod.CROSSWALK, external
                    )

        # 3. Name-based fallback, only against nflverse and only with support.
        if self._index is not None and normalized:
            resolved_or_conflict = self._resolve_by_name(
                sleeper_id=sleeper_id,
                name=name,
                normalized=normalized,
                position=position,
                team=team,
                external=external,
            )
            if resolved_or_conflict is not None:
                return resolved_or_conflict

        # 4. No nflverse anchor. The player still gets a durable internal id -
        #    an August rookie is real even with no history - but is flagged as
        #    unanchored so downstream code never expects historical features.
        return ResolvedIdentity(
            player_uuid=make_player_uuid(gsis_id=None, sleeper_id=sleeper_id),
            method=ResolutionMethod.UNRESOLVED,
            confidence=0.0,
            sleeper_id=sleeper_id,
            gsis_id=None,
            external_ids=external,
        )

    def _resolve_by_name(
        self,
        *,
        sleeper_id: str,
        name: str,
        normalized: str,
        position: Position,
        team: str | None,
        external: dict[str, str],
    ) -> ResolvedIdentity | IdentityConflict | None:
        """Attempt a supported name match. ``None`` means no candidate at all."""
        assert self._index is not None
        position_key = position.value
        team_key = (team or "").upper()

        # Name + team + position is a genuinely discriminating composite.
        if team_key:
            candidates = self._index.by_name_team_position.get(
                (normalized, team_key, position_key), ()
            )
            if len(candidates) == 1:
                return self._build(
                    sleeper_id,
                    candidates[0],
                    ResolutionMethod.NAME_TEAM_POSITION,
                    external,
                )
            if len(candidates) > 1:
                return IdentityConflict(
                    sleeper_id=sleeper_id,
                    name=name,
                    normalized_name=normalized,
                    team=team,
                    position=position,
                    reason=ConflictReason.AMBIGUOUS_MATCH,
                    candidate_gsis_ids=candidates,
                    detail="multiple nflverse players share this name, team and position",
                )

        # Name + position alone is weak; it is recorded, never auto-applied.
        candidates = self._index.by_name_position.get((normalized, position_key), ())
        if len(candidates) == 1:
            return IdentityConflict(
                sleeper_id=sleeper_id,
                name=name,
                normalized_name=normalized,
                team=team,
                position=position,
                reason=ConflictReason.LOW_CONFIDENCE,
                candidate_gsis_ids=candidates,
                detail=(
                    "single name+position match, but no team agreement; "
                    f"confidence {_CONFIDENCE['NAME_POSITION']:.2f} is below the "
                    f"{MIN_AUTO_ACCEPT_CONFIDENCE:.2f} auto-accept threshold"
                ),
            )
        if len(candidates) > 1:
            return IdentityConflict(
                sleeper_id=sleeper_id,
                name=name,
                normalized_name=normalized,
                team=team,
                position=position,
                reason=ConflictReason.AMBIGUOUS_MATCH,
                candidate_gsis_ids=candidates,
                detail="multiple nflverse players share this name and position",
            )
        return None

    def _build(
        self,
        sleeper_id: str,
        gsis_id: str,
        method: ResolutionMethod,
        external: dict[str, str],
    ) -> ResolvedIdentity:
        """Assemble a resolved identity.

        Identifiers are merged from every source that knows this player, with
        the crosswalk taking precedence over nflverse where both have a value -
        the crosswalk is fantasy-specific and updated weekly.
        """
        merged: dict[str, str] = {}
        if self._index is not None:
            merged.update(self._index.external_ids_by_gsis.get(gsis_id, {}))
        merged.update(external)
        merged["gsis_id"] = gsis_id
        merged["sleeper_id"] = sleeper_id
        return ResolvedIdentity(
            player_uuid=make_player_uuid(gsis_id=gsis_id, sleeper_id=sleeper_id),
            method=method,
            confidence=_CONFIDENCE.get(method.value, 0.0),
            sleeper_id=sleeper_id,
            gsis_id=gsis_id,
            external_ids=merged,
        )

    def resolve_many(self, players: Iterable[Mapping[str, Any]]) -> ResolutionReport:
        """Resolve a batch of Sleeper player records.

        Args:
            players: Raw Sleeper player dicts.

        Returns:
            A report holding resolved identities, conflicts, and method counts,
            so ingestion can log exactly how the pool was linked.
        """
        resolved: list[ResolvedIdentity] = []
        conflicts: list[IdentityConflict] = []
        counts: dict[ResolutionMethod, int] = {}

        for raw in players:
            sleeper_id = clean_token(raw.get("player_id"))
            if sleeper_id is None:
                continue
            name = (
                clean_token(raw.get("full_name"))
                or " ".join(
                    part
                    for part in (
                        clean_token(raw.get("first_name")),
                        clean_token(raw.get("last_name")),
                    )
                    if part
                )
                or ""
            )
            outcome = self.resolve(
                sleeper_id=sleeper_id,
                name=name,
                position=Position.parse(clean_token(raw.get("position"))),
                team=clean_token(raw.get("team")),
                gsis_id=clean_token(raw.get("gsis_id")),
            )
            if isinstance(outcome, IdentityConflict):
                conflicts.append(outcome)
            else:
                resolved.append(outcome)
                counts[outcome.method] = counts.get(outcome.method, 0) + 1

        report = ResolutionReport(
            resolved=tuple(resolved),
            conflicts=tuple(conflicts),
            method_counts=counts,
        )
        log.info(
            "identity_resolution_complete",
            total=report.total,
            resolved=len(resolved),
            conflicts=len(conflicts),
            anchored_rate=round(report.anchored_rate, 4),
            methods={m.value: c for m, c in counts.items()},
        )
        return report
