"""Value objects shared across the draft engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum, unique

from fhe.core.health.models import (
    HealthAssessment,
    InjuryHistoryEvent,
    WorkloadSummary,
)
from fhe.core.schedule import PlayoffSchedule
from fhe.core.types import Position
from fhe.core.usage import UsageProfile


@dataclass(frozen=True, slots=True)
class DraftPick:
    """A single selection.

    ``pick_no`` is the overall pick number and is the idempotency key: a draft
    has exactly one pick at each number.

    ``draft_slot`` is the seat that owns the pick position, while ``roster_id``
    is the team that actually made it. These differ when a pick has been traded,
    which is why both are carried rather than one being derived from the other.
    """

    pick_no: int
    round_number: int
    draft_slot: int
    player_uuid: str
    roster_id: int | None = None
    picked_by: str | None = None
    is_keeper: bool = False
    source_player_id: str | None = None
    observed_at: datetime | None = None


@unique
class PickOutcome(StrEnum):
    """Result of applying a pick to draft state."""

    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class PickApplication:
    """What happened when a pick was applied.

    The live poller uses this instead of exceptions, because receiving the same
    pick twice is the *normal* case when polling, not an error.
    """

    pick: DraftPick
    outcome: PickOutcome
    existing: DraftPick | None = None

    @property
    def is_new(self) -> bool:
        """Whether this application changed draft state."""
        return self.outcome is PickOutcome.APPLIED


@dataclass(frozen=True, slots=True)
class ProvenancedValue:
    """A metric together with where it came from and when.

    Every number the war room displays carries one of these, so the UI can
    always answer "says who, and how old is this?".
    """

    value: float
    source: str
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DraftablePlayer:
    """A player as the draft engine sees them.

    All analytics inputs are optional. A player with no projection is ranked by
    ADP alone and flagged as such rather than being assigned an invented value.
    """

    player_uuid: str
    name: str
    position: Position
    team: str | None = None

    projected_points: float | None = None
    projection_source: str | None = None

    adp: float | None = None
    adp_stdev: float | None = None
    adp_source: str | None = None

    health: HealthAssessment | None = None
    # Carried alongside the assessment so the player drawer can render a
    # timeline and usage chart without a second round trip.
    injury_history: tuple[InjuryHistoryEvent, ...] = field(default=())
    workload: WorkloadSummary | None = None
    usage: UsageProfile | None = None
    playoff_schedule: PlayoffSchedule | None = None
    bye_week: int | None = None
    age: float | None = None
    years_experience: int | None = None

    # Sleeper's ``search_rank`` is a useful popularity prior when no ADP exists.
    popularity_rank: int | None = None

    @property
    def has_projection(self) -> bool:
        """Whether a real projection is available for this player."""
        return self.projected_points is not None


@dataclass(frozen=True, slots=True)
class TeamRoster:
    """One fantasy team's drafted players."""

    draft_slot: int
    roster_id: int | None = None
    player_uuids: tuple[str, ...] = field(default=())

    def with_player(self, player_uuid: str) -> TeamRoster:
        """Return a copy with ``player_uuid`` appended."""
        return TeamRoster(
            draft_slot=self.draft_slot,
            roster_id=self.roster_id,
            player_uuids=(*self.player_uuids, player_uuid),
        )
