"""League configuration and the replacement-level maths derived from it.

Replacement level is the single most important league-dependent quantity in the
system: it is what turns a raw projection into *value over replacement*, and it
is why a 12-team superflex league ranks quarterbacks completely differently from
a 10-team single-QB league.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import cached_property

from fhe.core.errors import LeagueConfigurationError
from fhe.core.types import (
    SLOT_ELIGIBILITY,
    DraftType,
    Position,
    RosterSlot,
    ScoringFormat,
)

# Positions whose scarcity and tier structure actually drive draft strategy.
# Kickers and defenses are excluded: they are near-interchangeable and their
# "scarcity" is never a reason to change a pick.
VALUED_POSITIONS: tuple[Position, ...] = (
    Position.QB,
    Position.RB,
    Position.WR,
    Position.TE,
)

# Every position that can occupy a roster spot. Replacement level MUST be
# computed across all of these, not just the strategically interesting ones:
# a position with no baseline gets a value-over-replacement equal to its entire
# projection, which is how a kicker ends up ranked in the first round.
ROSTERABLE_POSITIONS: tuple[Position, ...] = (
    *VALUED_POSITIONS,
    Position.K,
    Position.DEF,
)

# Minimum replacement rank floor. Without this, a league with zero dedicated TE
# slots would compute a replacement rank of 0 and make every TE infinitely
# valuable.
_MIN_REPLACEMENT_RANK = 1

# Share of SUPER_FLEX slots assumed to be filled by a quarterback.
#
# Unlike an RB/WR/TE flex - where the marginal starter is genuinely contested -
# a superflex slot is filled by a QB almost every week, because even a low-end
# starting QB out-scores a flex-calibre skill player under every mainstream
# scoring format. Allocating this slot proportionally to dedicated starters (the
# rule used for ordinary flexes) would put QB replacement level around QB14 in a
# 12-team superflex league, which is far too shallow and systematically
# under-values quarterbacks in exactly the format where they matter most.
#
# The residual share is distributed across the remaining eligible positions by
# the same proportional rule used for ordinary flex slots.
_SUPERFLEX_QB_SHARE = 0.85


# NOTE: no ``slots=True`` here - the derived properties below use
# ``functools.cached_property``, which needs an instance ``__dict__``.
@dataclass(frozen=True)
class LeagueSettings:
    """Everything the engine needs to know about a league's shape.

    Args:
        team_count: Number of fantasy teams.
        roster_slots: The league's lineup configuration, one entry per roster
            spot, in the provider's declared order (Sleeper ``roster_positions``).
        scoring_format: Reception-scoring family.
        draft_type: Snake, linear, or auction.
        rounds: Number of draft rounds. Defaults to ``len(roster_slots)``.
        user_draft_slot: The user's 1-indexed seat, when known.
    """

    team_count: int
    roster_slots: tuple[RosterSlot, ...]
    scoring_format: ScoringFormat = ScoringFormat.HALF_PPR
    draft_type: DraftType = DraftType.SNAKE
    rounds: int | None = None
    user_draft_slot: int | None = None
    unrecognised_slot_tokens: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.team_count < 2:
            raise LeagueConfigurationError(f"team_count must be at least 2, got {self.team_count}")
        if self.team_count > 32:
            raise LeagueConfigurationError(
                f"team_count above 32 is not supported, got {self.team_count}"
            )
        if not self.roster_slots:
            raise LeagueConfigurationError("roster_slots must not be empty")
        if self.user_draft_slot is not None and not (1 <= self.user_draft_slot <= self.team_count):
            raise LeagueConfigurationError(
                f"user_draft_slot {self.user_draft_slot} outside 1..{self.team_count}"
            )
        if self.rounds is not None and self.rounds < 1:
            raise LeagueConfigurationError(f"rounds must be positive, got {self.rounds}")

    # ------------------------------------------------------------------ build

    @classmethod
    def from_tokens(
        cls,
        *,
        team_count: int,
        roster_position_tokens: list[str] | tuple[str, ...],
        scoring_format: ScoringFormat = ScoringFormat.HALF_PPR,
        draft_type: DraftType = DraftType.SNAKE,
        rounds: int | None = None,
        user_draft_slot: int | None = None,
    ) -> LeagueSettings:
        """Build settings from raw provider roster-position strings.

        Unrecognised tokens are preserved in ``unrecognised_slot_tokens`` rather
        than discarded, so an IDP or otherwise exotic league surfaces a warning
        in diagnostics instead of silently mis-sizing replacement level.
        """
        slots: list[RosterSlot] = []
        unknown: list[str] = []
        for token in roster_position_tokens:
            slot = RosterSlot.parse(token)
            if slot is RosterSlot.UNKNOWN:
                unknown.append(token)
            slots.append(slot)
        return cls(
            team_count=team_count,
            roster_slots=tuple(slots),
            scoring_format=scoring_format,
            draft_type=draft_type,
            rounds=rounds,
            user_draft_slot=user_draft_slot,
            unrecognised_slot_tokens=tuple(unknown),
        )

    # -------------------------------------------------------------- geometry

    @property
    def total_rounds(self) -> int:
        """Number of rounds, defaulting to one pick per roster spot."""
        return self.rounds if self.rounds is not None else len(self.roster_slots)

    @property
    def total_picks(self) -> int:
        """Total picks in the whole draft."""
        return self.total_rounds * self.team_count

    @cached_property
    def slot_counts(self) -> dict[RosterSlot, int]:
        """How many of each slot the lineup contains."""
        return dict(Counter(self.roster_slots))

    @cached_property
    def starting_slots(self) -> tuple[RosterSlot, ...]:
        """Only the slots that contribute to a weekly starting lineup."""
        return tuple(s for s in self.roster_slots if s.is_starting_slot)

    @property
    def starters_per_team(self) -> int:
        """Number of weekly starting spots per team."""
        return len(self.starting_slots)

    @property
    def bench_per_team(self) -> int:
        """Number of bench spots per team."""
        return self.slot_counts.get(RosterSlot.BENCH, 0)

    @property
    def is_superflex(self) -> bool:
        """Whether a quarterback may be started in a flex spot."""
        return RosterSlot.SUPER_FLEX in self.slot_counts

    # ----------------------------------------------------- replacement level

    @cached_property
    def dedicated_starters(self) -> dict[Position, int]:
        """Starting slots per team that only one position can fill."""
        counts: dict[Position, int] = dict.fromkeys(ROSTERABLE_POSITIONS, 0)
        for slot in self.starting_slots:
            eligible = SLOT_ELIGIBILITY.get(slot, frozenset())
            valued = [p for p in eligible if p in counts]
            if len(valued) == 1:
                counts[valued[0]] += 1
        return counts

    @cached_property
    def flex_allocation(self) -> dict[Position, float]:
        """Expected share of multi-position flex slots taken by each position.

        Ordinary flex spots (FLEX, REC_FLEX, WRRB_FLEX) are split across eligible
        positions in proportion to how many *dedicated* starting slots that
        position already has. In a standard 2RB/2WR/1TE/1FLEX league this sends
        40% of the flex to RB, 40% to WR and 20% to TE, which matches how flex
        spots are actually filled far better than an even three-way split.

        SUPER_FLEX is handled separately via :data:`_SUPERFLEX_QB_SHARE`, because
        a quarterback fills that slot in the overwhelming majority of lineups.

        Positions with no dedicated slots fall back to an even share of the
        residual so they are never allocated exactly zero.
        """
        allocation: dict[Position, float] = dict.fromkeys(ROSTERABLE_POSITIONS, 0.0)
        dedicated = self.dedicated_starters

        for slot in self.starting_slots:
            eligible = [p for p in SLOT_ELIGIBILITY.get(slot, frozenset()) if p in allocation]
            if len(eligible) <= 1:
                continue  # dedicated slot, already counted

            if slot is RosterSlot.SUPER_FLEX and Position.QB in eligible:
                allocation[Position.QB] += _SUPERFLEX_QB_SHARE
                residual = 1.0 - _SUPERFLEX_QB_SHARE
                others = [p for p in eligible if p is not Position.QB]
                self._distribute(allocation, others, dedicated, residual)
            else:
                self._distribute(allocation, eligible, dedicated, 1.0)
        return allocation

    @staticmethod
    def _distribute(
        allocation: dict[Position, float],
        positions: Sequence[Position],
        dedicated: dict[Position, int],
        total_share: float,
    ) -> None:
        """Split ``total_share`` across ``positions`` weighted by dedicated slots."""
        if not positions or total_share <= 0:
            return
        weights = {p: float(dedicated.get(p, 0)) for p in positions}
        weight_sum = sum(weights.values())
        if weight_sum <= 0:
            even = total_share / len(positions)
            for p in positions:
                allocation[p] += even
            return
        for p in positions:
            allocation[p] += total_share * weights[p] / weight_sum

    @cached_property
    def replacement_rank(self) -> dict[Position, int]:
        """Positional rank at which a player is considered replacement level.

        Covers every rosterable position, including kickers and defenses. A
        position the league does not start at all floors to rank 1, which makes
        its best player exactly replacement level and correctly gives the whole
        position a value-over-replacement of zero.

        ``replacement_rank[RB] == 29`` means the 29th-best running back is the
        baseline: a starter-calibre RB's value is measured as the projected
        points he produces *above* that player.

        Computed as ``team_count x (dedicated starters + expected flex share)``,
        rounded to the nearest whole player and floored at 1.
        """
        ranks: dict[Position, int] = {}
        flex = self.flex_allocation
        for position in ROSTERABLE_POSITIONS:
            starters = self.dedicated_starters.get(position, 0) + flex.get(position, 0.0)
            rank = round(self.team_count * starters)
            ranks[position] = max(_MIN_REPLACEMENT_RANK, int(rank))
        return ranks

    # --------------------------------------------------------- draft ordering

    def pick_number(self, draft_slot: int, round_number: int) -> int:
        """Overall pick number for a seat in a given round (both 1-indexed).

        Snake drafts reverse the seat order on even rounds; linear drafts do not.
        Auction drafts have no pick order, so this raises.
        """
        if not (1 <= draft_slot <= self.team_count):
            raise LeagueConfigurationError(f"draft_slot {draft_slot} outside 1..{self.team_count}")
        if round_number < 1:
            raise LeagueConfigurationError(f"round_number must be >= 1, got {round_number}")
        if self.draft_type is DraftType.AUCTION:
            raise LeagueConfigurationError("auction drafts have no deterministic pick order")

        base = (round_number - 1) * self.team_count
        if self.draft_type is DraftType.SNAKE and round_number % 2 == 0:
            return base + (self.team_count - draft_slot + 1)
        return base + draft_slot

    def picks_for_slot(self, draft_slot: int) -> tuple[int, ...]:
        """Every overall pick number belonging to a seat, in order."""
        return tuple(self.pick_number(draft_slot, rnd) for rnd in range(1, self.total_rounds + 1))

    def next_pick_for_slot(self, draft_slot: int, after_pick: int) -> int | None:
        """The seat's next pick strictly after ``after_pick``, or ``None`` if done."""
        for pick in self.picks_for_slot(draft_slot):
            if pick > after_pick:
                return pick
        return None

    def picks_until_next_turn(self, draft_slot: int, current_pick: int) -> int | None:
        """How many other selections happen before this seat picks again.

        ``current_pick`` is the pick number about to be made. A return value of 0
        means it is this seat's turn right now.
        """
        next_pick = self.next_pick_for_slot(draft_slot, after_pick=current_pick - 1)
        if next_pick is None:
            return None
        return next_pick - current_pick
