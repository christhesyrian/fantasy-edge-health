"""Draft session registry.

A session pairs a draft state with the player pool it is being evaluated
against, and owns the recomputation and event publication that follow a pick.

Storage
-------
Sessions live in memory. That is the right call for v1 and a deliberate limit
rather than an oversight:

* a mock draft is ephemeral and cheap to recreate from its seed;
* the deterministic simulator means a session can be rebuilt exactly from
  ``(seed, picks)`` if it is ever lost;
* persisting a live Sleeper draft is the *provider's* state, and it is already
  written to ``draft_picks`` by the poller.

The consequence is that sessions do not survive a restart and do not span
processes, which the readiness endpoint reports alongside the other
single-process degradations.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter

from fhe.api.events import DraftEvent, EventBus, EventType, SequenceCounter
from fhe.core.draft.board import DraftBoard
from fhe.core.draft.models import DraftablePlayer, DraftPick
from fhe.core.draft.service import evaluate_draft
from fhe.core.draft.state import DraftState
from fhe.core.draft.vorp import ReplacementBaseline, compute_replacement_baseline
from fhe.core.errors import DraftStateError
from fhe.core.league import LeagueSettings
from fhe.core.simulation import MockDraftSimulator, SimulationConfig
from fhe.db.base import utcnow
from fhe.observability import RECOMMENDATION_LATENCY, get_logger

log = get_logger(__name__)

# Sessions idle longer than this are eligible for eviction, so a long-running
# server does not accumulate abandoned mock drafts.
SESSION_IDLE_TIMEOUT_SECONDS = 6 * 60 * 60
MAX_SESSIONS = 200


class SessionNotFoundError(LookupError):
    """No session exists with the given id."""


@dataclass
class DraftSession:
    """One live or simulated draft, with everything needed to evaluate it.

    Mutable on purpose: a live draft's state advances underneath it, and a
    reconnect refreshes the pool without replacing the session identity that
    subscribers and the poller are attached to.
    """

    session_id: str
    league: LeagueSettings
    pool: tuple[DraftablePlayer, ...]
    baseline: ReplacementBaseline
    is_demo: bool
    seed: int | None = None
    simulator: MockDraftSimulator | None = None
    state: DraftState | None = None
    created_at: datetime = field(default_factory=utcnow)
    last_touched_at: datetime = field(default_factory=utcnow)
    # The provider's own word on whether the draft is finished. A draft can end
    # before every slot is filled - abandoned, or shortened - so pick arithmetic
    # alone would keep reporting it as live forever.
    provider_status: str | None = None
    # Gaps in the underlying data, surfaced on the board so a user is never
    # shown a confident ranking built on nothing.
    pool_warnings: tuple[str, ...] = field(default=())
    last_board: DraftBoard | None = None
    last_computation_ms: float | None = None

    @property
    def draft_state(self) -> DraftState:
        """The authoritative draft state, whichever source owns it."""
        if self.simulator is not None:
            return self.simulator.state
        if self.state is None:
            raise DraftStateError("session has neither a simulator nor a draft state")
        return self.state

    @property
    def players_by_uuid(self) -> dict[str, DraftablePlayer]:
        """Pool indexed for lookup."""
        return {p.player_uuid: p for p in self.pool}

    @property
    def user_draft_slot(self) -> int | None:
        """The seat the user occupies."""
        if self.simulator is not None:
            return self.simulator.user_draft_slot
        return self.league.user_draft_slot

    @property
    def is_complete(self) -> bool:
        """Whether the draft is over.

        The provider's status wins when it has one: it knows about drafts that
        ended early, which pick arithmetic cannot infer.
        """
        if self.provider_status:
            return self.provider_status.lower() == "complete"
        return self.draft_state.is_complete

    @property
    def is_user_on_the_clock(self) -> bool:
        """Whether the next pick belongs to the user."""
        if self.simulator is not None:
            return self.simulator.is_user_on_the_clock
        slot = self.user_draft_slot
        if slot is None:
            return False
        return self.draft_state.picks_until_slot_turn(slot) == 0

    def evaluate(self) -> DraftBoard:
        """Recompute the board and record how long it took."""
        started = perf_counter()
        board = evaluate_draft(
            self.draft_state,
            self.pool,
            user_draft_slot=self.user_draft_slot,
            baseline=self.baseline,
        )
        elapsed = perf_counter() - started
        RECOMMENDATION_LATENCY.observe(elapsed)
        self.last_board = board
        self.last_computation_ms = round(elapsed * 1000, 2)
        self.last_touched_at = utcnow()
        return board


class DraftSessionRegistry:
    """Creates, stores, and advances draft sessions."""

    def __init__(self, event_bus: EventBus) -> None:
        self._sessions: dict[str, DraftSession] = {}
        self._bus = event_bus
        self._sequence = SequenceCounter()
        self._lock = asyncio.Lock()

    @property
    def event_bus(self) -> EventBus:
        """The bus sessions publish onto."""
        return self._bus

    @property
    def sequence(self) -> SequenceCounter:
        """Shared event sequence counter.

        Exposed so an external publisher - the live poller - numbers its events
        on the same sequence as the registry's own, which is what lets a client
        detect a gap across both.
        """
        return self._sequence

    @property
    def count(self) -> int:
        """How many sessions are held."""
        return len(self._sessions)

    def register_live(
        self,
        *,
        session_id: str,
        league: LeagueSettings,
        pool: tuple[DraftablePlayer, ...],
        state: DraftState,
        baseline: ReplacementBaseline,
        provider_status: str | None = None,
        pool_warnings: tuple[str, ...] = (),
    ) -> DraftSession:
        """Register a session backed by a live provider draft.

        Keyed by the provider's own draft id rather than a fresh uuid, so
        reconnecting to the same draft resumes the existing session instead of
        creating a second one that would poll the provider twice.
        """
        existing = self._sessions.get(session_id)
        if existing is not None:
            # Refresh state and pool, since both may have moved on, but keep the
            # session identity that subscribers and the poller are attached to.
            existing.state = state
            existing.pool = pool
            existing.baseline = baseline
            existing.provider_status = provider_status
            existing.pool_warnings = pool_warnings
            existing.last_touched_at = utcnow()
            log.info("live_session_refreshed", session_id=session_id)
            return existing

        self._evict_if_needed()
        session = DraftSession(
            session_id=session_id,
            league=league,
            pool=pool,
            baseline=baseline,
            is_demo=False,
            state=state,
            provider_status=provider_status,
            pool_warnings=pool_warnings,
        )
        self._sessions[session_id] = session
        log.info(
            "live_session_registered",
            session_id=session_id,
            team_count=league.team_count,
            picks=state.pick_count,
        )
        return session

    async def publish_board_update(self, session: DraftSession) -> None:
        """Recompute and announce a board change for an externally-driven draft.

        The live poller owns draft state; this is how it asks the session to
        re-evaluate and tell its subscribers.
        """
        await self._publish_board(session)

    async def create_simulation(
        self,
        league: LeagueSettings,
        pool: tuple[DraftablePlayer, ...],
        *,
        seed: int,
        temperature: float = 3.0,
        user_draft_slot: int | None = None,
    ) -> DraftSession:
        """Start a seeded mock draft."""
        async with self._lock:
            self._evict_if_needed()
            session_id = str(uuid.uuid4())
            simulator = MockDraftSimulator(
                league,
                pool,
                config=SimulationConfig(seed=seed, temperature=temperature),
                user_draft_slot=(
                    user_draft_slot if user_draft_slot is not None else league.user_draft_slot
                ),
            )
            session = DraftSession(
                session_id=session_id,
                league=league,
                pool=pool,
                baseline=compute_replacement_baseline(pool, league),
                is_demo=True,
                seed=seed,
                simulator=simulator,
            )
            self._sessions[session_id] = session

        log.info(
            "simulation_created",
            session_id=session_id,
            seed=seed,
            team_count=league.team_count,
            user_slot=session.user_draft_slot,
        )
        return session

    def get(self, session_id: str) -> DraftSession:
        """Fetch a session.

        Raises:
            SessionNotFoundError: If the id is unknown or the session expired.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        session.last_touched_at = utcnow()
        return session

    def remove(self, session_id: str) -> None:
        """Discard a session if present."""
        self._sessions.pop(session_id, None)

    def _evict_if_needed(self) -> None:
        """Drop the least recently touched sessions when at capacity."""
        if len(self._sessions) < MAX_SESSIONS:
            return
        ordered = sorted(self._sessions.items(), key=lambda kv: kv[1].last_touched_at)
        for session_id, _ in ordered[: max(1, len(ordered) // 4)]:
            del self._sessions[session_id]
        log.info("sessions_evicted", remaining=len(self._sessions))

    # ----------------------------------------------------------- advancement

    async def advance(
        self, session: DraftSession, *, picks: int, stop_at_user_turn: bool
    ) -> list[DraftPick]:
        """Run computer picks and publish an event for each.

        Args:
            session: The session to advance.
            picks: Maximum number of computer picks to make.
            stop_at_user_turn: Stop as soon as the user is on the clock.

        Returns:
            The picks that were made, in order.
        """
        if session.simulator is None:
            raise DraftStateError("only simulated drafts can be advanced")

        made: list[DraftPick] = []
        for _ in range(picks):
            if stop_at_user_turn and session.simulator.is_user_on_the_clock:
                break
            pick = session.simulator.advance()
            if pick is None:
                break
            made.append(pick)

        for pick in made:
            await self._publish_pick(session, pick)

        if made:
            await self._publish_board(session)
        if session.simulator.is_complete:
            await self._publish(session, EventType.DRAFT_COMPLETE, {})
        return made

    async def make_user_pick(self, session: DraftSession, player_uuid: str) -> DraftPick:
        """Record the user's selection and publish the resulting events."""
        if session.simulator is None:
            raise DraftStateError("only simulated drafts accept a user pick here")
        pick = session.simulator.draft_player(player_uuid)
        await self._publish_pick(session, pick)
        await self._publish_board(session)
        return pick

    async def reset(self, session: DraftSession) -> None:
        """Return a simulation to its pristine seeded state."""
        if session.simulator is None:
            raise DraftStateError("only simulated drafts can be reset")
        session.simulator.reset()
        session.last_board = None
        await self._publish_board(session)

    # ------------------------------------------------------------- publishing

    async def _publish_pick(self, session: DraftSession, pick: DraftPick) -> None:
        """Announce a completed selection."""
        player = session.players_by_uuid.get(pick.player_uuid)
        await self._publish(
            session,
            EventType.PICK_MADE,
            {
                "pick_no": pick.pick_no,
                "round": pick.round_number,
                "draft_slot": pick.draft_slot,
                "player_uuid": pick.player_uuid,
                "player_name": player.name if player else None,
                "position": player.position.value if player else None,
                "team": player.team if player else None,
            },
        )

    async def _publish_board(self, session: DraftSession) -> None:
        """Recompute and announce that the board changed."""
        board = session.evaluate()
        await self._publish(
            session,
            EventType.BOARD_UPDATED,
            {
                "current_pick": board.current_pick,
                "picks_until_user_turn": board.picks_until_user_turn,
                "best_player_uuid": board.best_pick.player_uuid if board.best_pick else None,
                "computation_ms": session.last_computation_ms,
            },
        )

    async def _publish(
        self, session: DraftSession, event_type: EventType, payload: dict[str, object]
    ) -> None:
        """Publish an event on the session's channel."""
        sequence = await self._sequence.next(session.session_id)
        await self._bus.publish(
            DraftEvent(
                draft_id=session.session_id,
                type=event_type,
                sequence=sequence,
                payload=dict(payload),
            )
        )

    def current_sequence(self, session_id: str) -> int:
        """Latest event sequence issued for a session."""
        return self._sequence.current(session_id)
