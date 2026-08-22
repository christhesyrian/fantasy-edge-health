"""Mock draft simulator.

The simulator exists to rehearse the real thing. It drives the *same*
:class:`~fhe.core.draft.state.DraftState` and the *same* recommendation engine
the live Sleeper integration uses, so a mock draft is a genuine test of the
production code path rather than a parallel implementation that can drift.

Opponent behaviour
------------------
Each computer team picks by sampling from a short window of the best available
players, weighted by:

* **ADP proximity** - the market's consensus, softened by a temperature so the
  room does not draft in a rigid ADP order the way no real league ever does.
* **Roster need** - a team with two quarterbacks stops taking quarterbacks.
* **Positional tendency** - a per-league bias letting the caller simulate a
  running-back-heavy or a receiver-heavy room.

Everything is driven by a seeded :class:`random.Random`, so a given seed always
produces the identical draft. That is what makes the simulator usable in
automated tests rather than only as a demo toy.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Final

from fhe.core.draft.models import DraftablePlayer, DraftPick
from fhe.core.draft.roster import compute_roster_need
from fhe.core.draft.state import DraftState
from fhe.core.errors import DraftStateError
from fhe.core.league import LeagueSettings
from fhe.core.types import Position

# How many of the best available players an opponent will consider.
_DEFAULT_CONSIDERATION_WINDOW: Final = 10
# Softmax temperature over ADP rank. Higher means a more chaotic room.
_DEFAULT_TEMPERATURE: Final = 3.0
# Opponents avoid kickers and defenses until this fraction of the draft is done,
# which is what real drafters do and what makes the late rounds look right.
_LATE_ROUND_POSITIONS: Final[frozenset[Position]] = frozenset({Position.K, Position.DEF})
_LATE_ROUND_START_FRACTION: Final = 0.82
_LATE_ROUND_SUPPRESSION: Final = 0.001


class _Unset:
    """Sentinel distinguishing "argument omitted" from "explicitly None".

    ``user_draft_slot=None`` must mean *there is no human seat* - a fully
    autopiloted draft - which a plain ``or`` fallback to the league default
    silently turns back into "seat 5".
    """

    __slots__ = ()


_UNSET: Final = _Unset()


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Parameters controlling opponent behaviour."""

    seed: int = 7
    consideration_window: int = _DEFAULT_CONSIDERATION_WINDOW
    temperature: float = _DEFAULT_TEMPERATURE
    position_tendency: dict[Position, float] = field(default_factory=dict)

    def tendency_for(self, position: Position) -> float:
        """Multiplier applied to a position's selection weight."""
        return self.position_tendency.get(position, 1.0)


class MockDraftSimulator:
    """A seeded, steppable mock draft.

    Args:
        league: League configuration, including the user's draft slot.
        pool: The full player pool.
        config: Opponent behaviour parameters.
        user_draft_slot: Overrides ``league.user_draft_slot``. Pass ``None``
            explicitly to run a fully autopiloted draft with no human seat;
            omit it to inherit the league's configured slot.
    """

    def __init__(
        self,
        league: LeagueSettings,
        pool: tuple[DraftablePlayer, ...],
        *,
        config: SimulationConfig | None = None,
        user_draft_slot: int | _Unset | None = _UNSET,
    ) -> None:
        self._league = league
        self._pool = pool
        self._config = config or SimulationConfig()
        self._user_slot: int | None = (
            league.user_draft_slot if isinstance(user_draft_slot, _Unset) else user_draft_slot
        )
        self._players_by_uuid = {p.player_uuid: p for p in pool}
        self._state = DraftState(league, draft_id=f"sim-{self._config.seed}")
        self._rng = random.Random(self._config.seed)  # noqa: S311 - simulation, not crypto

    # ------------------------------------------------------------------ reads

    @property
    def state(self) -> DraftState:
        """The live draft state."""
        return self._state

    @property
    def league(self) -> LeagueSettings:
        """League configuration."""
        return self._league

    @property
    def user_draft_slot(self) -> int | None:
        """The seat the human occupies, if any."""
        return self._user_slot

    @property
    def is_complete(self) -> bool:
        """Whether every pick has been made."""
        return self._state.is_complete

    @property
    def available(self) -> tuple[DraftablePlayer, ...]:
        """Players still on the board, in ADP order."""
        drafted = self._state.drafted_player_uuids
        return tuple(p for p in self._pool if p.player_uuid not in drafted)

    @property
    def is_user_on_the_clock(self) -> bool:
        """Whether the next pick belongs to the user."""
        if self._user_slot is None:
            return False
        return self._state.picks_until_slot_turn(self._user_slot) == 0

    def slot_on_the_clock(self) -> int | None:
        """Which seat owns the next pick."""
        pick_no = self._state.current_pick_number
        if pick_no is None:
            return None
        round_number = (pick_no - 1) // self._league.team_count + 1
        for slot in range(1, self._league.team_count + 1):
            if self._league.pick_number(slot, round_number) == pick_no:
                return slot
        return None

    # ----------------------------------------------------------------- writes

    def reset(self) -> None:
        """Return to a pristine pre-draft state with the original seed."""
        self._state = DraftState(self._league, draft_id=f"sim-{self._config.seed}")
        self._rng = random.Random(self._config.seed)  # noqa: S311

    def advance(self) -> DraftPick | None:
        """Make exactly one computer pick.

        Returns ``None`` when the draft is over or when it is the user's turn -
        the simulator never picks on the user's behalf.
        """
        if self._state.is_complete or self.is_user_on_the_clock:
            return None
        return self._make_pick(self._choose_for_computer())

    def advance_to_user_turn(self, *, max_picks: int = 1000) -> tuple[DraftPick, ...]:
        """Run computer picks until the user is on the clock or the draft ends.

        Args:
            max_picks: Hard stop that guarantees termination even if a future
                change introduced a state machine that could not progress.
        """
        made: list[DraftPick] = []
        for _ in range(max_picks):
            pick = self.advance()
            if pick is None:
                break
            made.append(pick)
        return tuple(made)

    def draft_player(self, player_uuid: str) -> DraftPick:
        """Make the user's selection.

        Raises:
            DraftStateError: If it is not the user's turn, or the player is gone.
        """
        if self._user_slot is None:
            raise DraftStateError("simulation has no user draft slot configured")
        if not self.is_user_on_the_clock:
            raise DraftStateError("it is not the user's turn to pick")
        player = self._players_by_uuid.get(player_uuid)
        if player is None:
            raise DraftStateError(f"unknown player {player_uuid!r}")
        if self._state.is_drafted(player_uuid):
            raise DraftStateError(f"player {player_uuid!r} has already been drafted")
        return self._make_pick(player)

    # ---------------------------------------------------------------- internals

    def _make_pick(self, player: DraftablePlayer) -> DraftPick:
        """Record a selection for whichever seat is on the clock."""
        pick_no = self._state.current_pick_number
        slot = self.slot_on_the_clock()
        if pick_no is None or slot is None:
            raise DraftStateError("draft is already complete")

        pick = DraftPick(
            pick_no=pick_no,
            round_number=(pick_no - 1) // self._league.team_count + 1,
            draft_slot=slot,
            player_uuid=player.player_uuid,
            roster_id=slot,
            source_player_id=player.player_uuid,
        )
        application = self._state.apply_pick(pick)
        if not application.is_new:
            raise DraftStateError(
                f"simulator produced a non-applicable pick: {application.outcome}"
            )
        return pick

    def _choose_for_computer(self) -> DraftablePlayer:
        """Sample an opponent's selection from the top of the board."""
        available = self.available
        if not available:
            raise DraftStateError("no players remain to draft")

        slot = self.slot_on_the_clock()
        assert slot is not None  # guarded by callers
        drafted_positions = [
            self._players_by_uuid[uuid].position
            for uuid in self._state.roster(slot).player_uuids
            if uuid in self._players_by_uuid
        ]
        need = compute_roster_need(self._league, drafted_positions)

        progress = self._state.pick_count / max(1, self._league.total_picks)
        window = available[: self._config.consideration_window]

        weights: list[float] = []
        for index, player in enumerate(window):
            # Softmax over position within the consideration window.
            weight = math.exp(-index / max(0.1, self._config.temperature))
            weight *= max(0.05, need.need_for(player.position))
            weight *= self._config.tendency_for(player.position)
            if player.position in _LATE_ROUND_POSITIONS and progress < _LATE_ROUND_START_FRACTION:
                weight *= _LATE_ROUND_SUPPRESSION
            weights.append(max(1e-9, weight))

        return self._rng.choices(window, weights=weights, k=1)[0]
