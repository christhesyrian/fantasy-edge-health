"""Draft state: the authoritative, idempotent record of what has been picked.

Live polling makes the same guarantees hard: the provider re-sends picks the
system already has, can return them out of order, and can deliver several at
once between two polls. This module is the single place that resolves all of
that, and it does so deterministically so the behaviour is unit-testable without
a network.

Invariants
----------
* A pick number appears at most once.
* A player is drafted at most once.
* Applying the same payload twice changes nothing.
* Picks are always stored in pick-number order regardless of arrival order.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from fhe.core.draft.models import (
    DraftPick,
    PickApplication,
    PickOutcome,
    TeamRoster,
)
from fhe.core.errors import DraftStateError
from fhe.core.league import LeagueSettings


class DraftState:
    """Mutable draft state with idempotent pick application.

    The class is intentionally mutable - it models a live, evolving draft - but
    every read accessor returns immutable snapshots so callers cannot corrupt it.
    """

    def __init__(self, league: LeagueSettings, *, draft_id: str | None = None) -> None:
        self._league = league
        self._draft_id = draft_id
        self._picks_by_no: dict[int, DraftPick] = {}
        self._player_to_pick: dict[str, int] = {}
        self._rosters: dict[int, TeamRoster] = {
            slot: TeamRoster(draft_slot=slot) for slot in range(1, league.team_count + 1)
        }

    # ------------------------------------------------------------------ reads

    @property
    def league(self) -> LeagueSettings:
        """The league configuration this draft runs under."""
        return self._league

    @property
    def draft_id(self) -> str | None:
        """Provider draft identifier, when the draft came from one."""
        return self._draft_id

    @property
    def picks(self) -> tuple[DraftPick, ...]:
        """Every pick made, in pick-number order."""
        return tuple(self._picks_by_no[n] for n in sorted(self._picks_by_no))

    @property
    def pick_count(self) -> int:
        """How many picks have been made."""
        return len(self._picks_by_no)

    @property
    def drafted_player_uuids(self) -> frozenset[str]:
        """Set of every player already selected."""
        return frozenset(self._player_to_pick)

    @property
    def is_complete(self) -> bool:
        """Whether every pick in the draft has been made."""
        return self.pick_count >= self._league.total_picks

    @property
    def current_pick_number(self) -> int | None:
        """The next pick number to be made, or ``None`` if the draft is over.

        This is the lowest *unfilled* pick number, not ``count + 1``: a draft can
        legitimately have gaps while picks are still arriving out of order.
        """
        if self.is_complete:
            return None
        for pick_no in range(1, self._league.total_picks + 1):
            if pick_no not in self._picks_by_no:
                return pick_no
        return None

    def roster(self, draft_slot: int) -> TeamRoster:
        """The roster belonging to a seat."""
        if draft_slot not in self._rosters:
            raise DraftStateError(f"draft_slot {draft_slot} outside 1..{self._league.team_count}")
        return self._rosters[draft_slot]

    @property
    def rosters(self) -> tuple[TeamRoster, ...]:
        """Every roster, ordered by draft slot."""
        return tuple(self._rosters[s] for s in sorted(self._rosters))

    def is_drafted(self, player_uuid: str) -> bool:
        """Whether a player has already been selected."""
        return player_uuid in self._player_to_pick

    def picks_until_slot_turn(self, draft_slot: int) -> int | None:
        """Selections remaining before ``draft_slot`` picks again.

        Returns 0 when it is that seat's turn right now, and ``None`` when the
        seat has no picks left.
        """
        current = self.current_pick_number
        if current is None:
            return None
        return self._league.picks_until_next_turn(draft_slot, current)

    def next_pick_number_for_slot(self, draft_slot: int) -> int | None:
        """The seat's next upcoming pick number, or ``None`` if it has none left."""
        current = self.current_pick_number
        if current is None:
            return None
        return self._league.next_pick_for_slot(draft_slot, after_pick=current - 1)

    # ----------------------------------------------------------------- writes

    def apply_pick(self, pick: DraftPick) -> PickApplication:
        """Apply one pick idempotently.

        Returns an application record rather than raising, because a repeated
        pick is the expected outcome of polling, not an error condition.

        * ``APPLIED``   - new pick, state changed.
        * ``DUPLICATE`` - identical pick already present, nothing changed.
        * ``CONFLICT``  - the pick number or player is already taken by a
          *different* selection. State is left untouched and the caller decides
          how to reconcile; the domain never silently overwrites history.
        """
        existing = self._picks_by_no.get(pick.pick_no)
        if existing is not None:
            if existing.player_uuid == pick.player_uuid:
                return PickApplication(pick=pick, outcome=PickOutcome.DUPLICATE, existing=existing)
            return PickApplication(pick=pick, outcome=PickOutcome.CONFLICT, existing=existing)

        prior_pick_no = self._player_to_pick.get(pick.player_uuid)
        if prior_pick_no is not None:
            return PickApplication(
                pick=pick,
                outcome=PickOutcome.CONFLICT,
                existing=self._picks_by_no[prior_pick_no],
            )

        if not (1 <= pick.pick_no <= self._league.total_picks):
            raise DraftStateError(f"pick_no {pick.pick_no} outside 1..{self._league.total_picks}")
        if pick.draft_slot not in self._rosters:
            raise DraftStateError(
                f"draft_slot {pick.draft_slot} outside 1..{self._league.team_count}"
            )

        self._picks_by_no[pick.pick_no] = pick
        self._player_to_pick[pick.player_uuid] = pick.pick_no
        self._rosters[pick.draft_slot] = self._rosters[pick.draft_slot].with_player(
            pick.player_uuid
        )
        return PickApplication(pick=pick, outcome=PickOutcome.APPLIED)

    def apply_picks(self, picks: Iterable[DraftPick]) -> tuple[PickApplication, ...]:
        """Apply a batch of picks in pick-number order.

        Sorting first is what makes an out-of-order provider response safe: the
        resulting state is identical no matter what order the batch arrived in.
        """
        ordered = sorted(picks, key=lambda p: p.pick_no)
        return tuple(self.apply_pick(p) for p in ordered)

    # ------------------------------------------------------------ convenience

    @classmethod
    def from_picks(
        cls,
        league: LeagueSettings,
        picks: Sequence[DraftPick],
        *,
        draft_id: str | None = None,
    ) -> DraftState:
        """Rebuild state from a full pick list (used on reconnect and replay)."""
        state = cls(league, draft_id=draft_id)
        state.apply_picks(picks)
        return state
