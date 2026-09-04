"""Connect a real Sleeper league and draft to a war-room session.

The step that turns "I have a Sleeper account" into a live board. It reads the
league and draft from the provider, records both, derives the league shape the
engine needs, loads the curated player pool, and seeds a session with whatever
picks have already happened.

Deliberately explicit: connecting is its own action, separate from browsing
leagues, so no user starts hitting the provider by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.api.services.draft_session import DraftSession, DraftSessionRegistry
from fhe.api.services.player_pool import PoolProvenance, load_player_pool
from fhe.core.draft.state import DraftState
from fhe.core.draft.vorp import compute_replacement_baseline
from fhe.core.errors import DomainError, LeagueConfigurationError
from fhe.core.league import LeagueSettings
from fhe.core.types import DraftStatus, DraftType, ScoringFormat
from fhe.data.ingest.lookup import load_external_id_map
from fhe.data.providers.sleeper import SleeperDraft, SleeperLeague, SleeperPick
from fhe.db.base import utcnow
from fhe.db.models.draft import Draft, FantasyLeague
from fhe.db.upsert import upsert_rows
from fhe.observability import get_logger
from fhe.worker.draft_poller import DraftBinding, PickRecorder, to_domain_pick

log = get_logger(__name__)

SOURCE = "sleeper"

# Maps a draft's ``slots_*`` setting onto roster-position tokens. A draft
# publishes its own lineup shape, which is what makes a league-less connection
# possible at all.
_SLOT_SETTING_TOKENS: dict[str, str] = {
    "slots_qb": "QB",
    "slots_rb": "RB",
    "slots_wr": "WR",
    "slots_te": "TE",
    "slots_k": "K",
    "slots_def": "DEF",
    "slots_flex": "FLEX",
    "slots_rec_flex": "REC_FLEX",
    "slots_wrrb_flex": "WRRB_FLEX",
    "slots_super_flex": "SUPER_FLEX",
    "slots_bn": "BN",
}

# Emission order for a reconstructed roster. Replacement level counts slots
# rather than reading their order, so this only needs to be conventional enough
# to be recognisable when displayed.
_SLOT_EMIT_ORDER: tuple[str, ...] = (
    "slots_qb",
    "slots_rb",
    "slots_wr",
    "slots_te",
    "slots_flex",
    "slots_rec_flex",
    "slots_wrrb_flex",
    "slots_super_flex",
    "slots_k",
    "slots_def",
    "slots_bn",
)

# A draft this far along is history, not something to follow live. Connecting to
# it is still allowed - reviewing a finished draft is useful - but no poller is
# started, because there is nothing left to observe.
COMPLETED_STATUSES = frozenset({"complete"})


class LeagueSource(Protocol):
    """The three capabilities connecting a draft needs from a provider.

    Declared narrowly rather than depending on the concrete client, so the
    connection flow states its real requirements and can be driven from
    fixtures without impersonating a whole API client.
    """

    async def get_league(self, league_id: str) -> SleeperLeague | None:
        """League metadata, or ``None`` if it does not exist."""
        ...

    async def get_draft(self, draft_id: str) -> SleeperDraft | None:
        """Draft metadata, or ``None`` if it does not exist."""
        ...

    async def get_draft_picks(self, draft_id: str) -> tuple[SleeperPick, ...]:
        """Every pick made so far, used to seed a mid-draft connection."""
        ...


class DraftNotFoundError(DomainError):
    """The requested league or draft does not exist at the provider."""


class EmptyPlayerPoolError(DomainError):
    """No players are available to rank.

    Distinct from a provider failure: the connection succeeded, but the database
    has nothing to reason about, which needs a different fix (run ingestion).
    """


@dataclass(frozen=True, slots=True)
class ConnectedDraft:
    """The result of connecting a live draft."""

    session_id: str
    league: LeagueSettings
    provider_league_id: str
    provider_draft_id: str
    league_name: str
    draft_status: DraftStatus
    user_draft_slot: int | None
    picks_already_made: int
    provenance: PoolProvenance

    @property
    def is_followable(self) -> bool:
        """Whether there is anything left to poll for."""
        return self.draft_status is not DraftStatus.COMPLETE


def scoring_format_of(league: SleeperLeague) -> ScoringFormat:
    """Infer the reception-scoring family from a league's actual settings.

    Sleeper publishes no format label, only the points-per-reception, which is
    the more truthful thing to read: a league calling itself "PPR" with 0.5 per
    reception is a half-PPR league whatever it is named.
    """
    reception = float(league.scoring_settings.get("rec", 0.0) or 0.0)
    if reception >= 0.75:
        return ScoringFormat.PPR
    if reception >= 0.25:
        return ScoringFormat.HALF_PPR
    return ScoringFormat.STANDARD


def roster_positions_from_draft(draft: SleeperDraft) -> list[str]:
    """Reconstruct a roster shape from a draft's own slot settings.

    A draft publishes ``slots_qb``, ``slots_rb`` and so on, which is enough to
    rebuild the lineup without the league. That matters because a league can be
    deleted, made private, or simply be one the user does not belong to, while
    its draft remains readable — verified against a real Sleeper draft whose
    league now returns 404.
    """
    positions: list[str] = []
    for key in _SLOT_EMIT_ORDER:
        raw = draft.settings.get(key)
        try:
            count = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            count = 0
        positions.extend([_SLOT_SETTING_TOKENS[key]] * max(0, count))
    return positions


def league_settings_from(
    league: SleeperLeague | None,
    draft: SleeperDraft,
    *,
    user_draft_slot: int | None,
) -> LeagueSettings:
    """Derive the engine's league configuration from provider payloads.

    The roster shape comes from the league when it is available, and the round
    count from the draft. They can legitimately disagree - a league with 15
    roster spots may run a 14-round draft with a keeper - so each is read from
    its own authority rather than inferred from the other.

    When the league is unavailable the shape is reconstructed from the draft's
    own slot settings, so a deleted or private league does not make an otherwise
    readable draft unusable.
    """
    roster = list(league.roster_positions) if league and league.roster_positions else []
    if not roster:
        roster = roster_positions_from_draft(draft)
    if not roster:
        raise LeagueConfigurationError(
            f"draft {draft.draft_id} publishes no roster shape, and its league "
            "is unavailable; cannot determine replacement level"
        )

    team_count = draft.team_count or (league.total_rosters if league else 0)
    if not team_count:
        raise LeagueConfigurationError(f"draft {draft.draft_id} publishes no team count")

    scoring = scoring_format_of(league) if league else ScoringFormat.parse(draft.scoring_type)

    return LeagueSettings.from_tokens(
        team_count=team_count,
        roster_position_tokens=roster,
        scoring_format=scoring,
        draft_type=DraftType.parse(draft.draft_type),
        rounds=draft.rounds,
        user_draft_slot=user_draft_slot,
    )


def resolve_user_slot(draft: SleeperDraft, user_id: str | None) -> int | None:
    """Find which seat a user occupies.

    Read from the draft's own ``draft_order`` map, which is the only
    authoritative statement of who sits where. Inferring it from roster order
    would silently break on a traded pick.
    """
    if not user_id or not draft.draft_order:
        return None
    slot = draft.draft_order.get(user_id)
    return int(slot) if slot is not None else None


async def _persist(
    session: AsyncSession,
    league: SleeperLeague | None,
    draft: SleeperDraft,
    settings: LeagueSettings,
    *,
    league_id: str,
    user_draft_slot: int | None,
) -> None:
    """Record the league and draft, idempotently.

    A league row is written even when the provider could not supply one, so the
    draft always has a parent and the shape it was reconstructed from is
    recorded rather than lost.
    """
    now = utcnow()
    season_text = league.season if league else draft.season
    await upsert_rows(
        session,
        FantasyLeague,
        [
            {
                "provider_league_id": league_id,
                "name": (league.name if league else None) or f"League {league_id}",
                "season": int(season_text) if season_text.isdigit() else 0,
                "status": league.status if league else "unavailable",
                "team_count": settings.team_count,
                "scoring_format": settings.scoring_format.value,
                "roster_positions": {
                    "positions": [slot.value for slot in settings.roster_slots],
                    "reconstructed_from_draft": league is None,
                },
                "scoring_settings": dict(league.scoring_settings) if league else {},
                "raw_settings": dict(league.settings) if league else dict(draft.settings),
                "is_demo": False,
                "source": SOURCE,
                "ingested_at": now,
                "observed_at": now,
                "source_updated_at": None,
            }
        ],
        conflict_columns=["provider_league_id", "source"],
    )

    league_row = (
        await session.execute(
            FantasyLeague.__table__.select().where(
                FantasyLeague.provider_league_id == league_id,
                FantasyLeague.source == SOURCE,
            )
        )
    ).first()
    if league_row is None:  # pragma: no cover - the upsert above guarantees a row
        raise DomainError("league row disappeared immediately after being written")

    start_time = (
        datetime.fromtimestamp(draft.start_time_ms / 1000, tz=UTC) if draft.start_time_ms else None
    )

    await upsert_rows(
        session,
        Draft,
        [
            {
                "league_id": league_row.id,
                "provider_draft_id": draft.draft_id,
                "status": draft.status,
                "draft_type": draft.draft_type,
                "rounds": settings.total_rounds,
                "season": int(draft.season) if draft.season.isdigit() else 0,
                "user_draft_slot": user_draft_slot,
                "slot_to_roster_id": dict(draft.slot_to_roster_id),
                "start_time": start_time,
                "last_polled_at": now,
                "last_pick_observed_at": None,
                "source": SOURCE,
                "ingested_at": now,
                "observed_at": now,
                "source_updated_at": None,
            }
        ],
        conflict_columns=["provider_draft_id", "source"],
    )
    await session.commit()


async def connect_sleeper_draft(
    session_factory: async_sessionmaker[AsyncSession],
    sleeper: LeagueSource,
    registry: DraftSessionRegistry,
    *,
    league_id: str,
    draft_id: str,
    user_id: str | None = None,
    user_draft_slot: int | None = None,
    as_of: date | None = None,
    recorder: PickRecorder | None = None,
) -> tuple[ConnectedDraft, DraftBinding, DraftSession]:
    """Connect a Sleeper draft and return a session ready to be polled.

    ``recorder`` stores the picks that had already been made when we connected.
    The poller records only what it newly applies, and those earlier picks are
    duplicates by the time it first polls, so without this a draft joined in the
    fourth round would keep no record of the first three.

    ``user_draft_slot`` overrides the seat that would be resolved from
    ``user_id``. Recovery after a restart passes the slot it already recorded,
    which is what lets it rebuild a session without knowing the user id — and
    means recovery and a fresh connection run the identical code path, so they
    cannot diverge.

    Raises:
        DraftNotFoundError: The league or draft does not exist.
        EmptyPlayerPoolError: The database holds no players to rank.
    """
    draft = await sleeper.get_draft(draft_id)
    if draft is None:
        raise DraftNotFoundError(f"no Sleeper draft {draft_id!r}")

    # The league is best-effort. It carries the roster shape and true scoring
    # settings, but a draft whose league has been deleted or made private is
    # still perfectly followable from the draft payload alone.
    league = await sleeper.get_league(league_id)
    if league is None:
        log.info(
            "league_unavailable_using_draft_settings",
            league_id=league_id,
            draft_id=draft_id,
        )

    user_slot = (
        user_draft_slot if user_draft_slot is not None else resolve_user_slot(draft, user_id)
    )
    settings = league_settings_from(league, draft, user_draft_slot=user_slot)
    season = int(draft.season) if draft.season.isdigit() else 0

    async with session_factory() as session:
        await _persist(
            session,
            league,
            draft,
            settings,
            league_id=league_id,
            user_draft_slot=user_slot,
        )

    async with session_factory() as session:
        pool, provenance = await load_player_pool(
            session,
            season=season,
            scoring_format=settings.scoring_format,
            as_of=as_of or utcnow().date(),
        )
        sleeper_to_uuid = await load_external_id_map(session, "sleeper_id")

    if not pool:
        raise EmptyPlayerPoolError(
            "No players in the database to rank. Run `fhe ingest players` before "
            "connecting a live draft."
        )

    binding = DraftBinding(
        draft_id=draft.draft_id,
        league=settings,
        user_draft_slot=user_slot,
        player_id_map=sleeper_to_uuid,
    )

    # Seed with whatever has already happened, so connecting mid-draft shows the
    # true board rather than an empty one.
    state = DraftState(settings, draft_id=draft.draft_id)
    existing = await sleeper.get_draft_picks(draft.draft_id)
    observed = utcnow()
    seeded = [to_domain_pick(pick, binding, observed_at=observed) for pick in existing]
    state.apply_picks(seeded)
    if recorder is not None and seeded:
        await recorder.record(draft.draft_id, seeded)

    session_record = registry.register_live(
        session_id=draft.draft_id,
        league=settings,
        pool=pool,
        state=state,
        baseline=compute_replacement_baseline(pool, settings),
        provider_status=draft.status,
        pool_warnings=provenance.warnings,
    )
    session_record.evaluate()

    connected = ConnectedDraft(
        session_id=session_record.session_id,
        league=settings,
        provider_league_id=league_id,
        provider_draft_id=draft.draft_id,
        league_name=(league.name if league else None) or f"League {league_id}",
        draft_status=DraftStatus.parse(draft.status),
        user_draft_slot=user_slot,
        picks_already_made=state.pick_count,
        provenance=provenance,
    )

    log.info(
        "sleeper_draft_connected",
        league_id=league_id,
        draft_id=draft.draft_id,
        user_slot=user_slot,
        picks_already_made=state.pick_count,
        pool_size=provenance.player_count,
        with_projection=provenance.with_projection,
    )
    return connected, binding, session_record
