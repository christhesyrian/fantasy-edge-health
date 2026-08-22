"""Deterministic draft intelligence.

The engine is pure: given a board state it returns the same recommendations
every time, with no I/O and no randomness. The live Sleeper draft, the mock
simulator, and the test suite all drive this identical code path, which is what
makes a simulated draft a genuine rehearsal rather than a separate implementation.
"""

from fhe.core.draft.board import AlertLevel, DraftAlert, DraftBoard, build_board
from fhe.core.draft.engine import (
    DraftContext,
    PlayerRecommendation,
    ScoreComponent,
    rank_board,
)
from fhe.core.draft.models import (
    DraftablePlayer,
    DraftPick,
    PickApplication,
    PickOutcome,
    TeamRoster,
)
from fhe.core.draft.roster import RosterNeed, compute_roster_need
from fhe.core.draft.scarcity import PositionScarcity, PositionTier, build_tiers, compute_scarcity
from fhe.core.draft.service import evaluate_draft
from fhe.core.draft.state import DraftState
from fhe.core.draft.survival import survival_probability, take_now_probability
from fhe.core.draft.vorp import (
    ReplacementBaseline,
    compute_replacement_baseline,
    value_over_replacement,
)

__all__ = [
    "AlertLevel",
    "DraftAlert",
    "DraftBoard",
    "DraftContext",
    "DraftPick",
    "DraftState",
    "DraftablePlayer",
    "PickApplication",
    "PickOutcome",
    "PlayerRecommendation",
    "PositionScarcity",
    "PositionTier",
    "ReplacementBaseline",
    "RosterNeed",
    "ScoreComponent",
    "TeamRoster",
    "build_board",
    "build_tiers",
    "compute_replacement_baseline",
    "compute_roster_need",
    "compute_scarcity",
    "evaluate_draft",
    "rank_board",
    "survival_probability",
    "take_now_probability",
    "value_over_replacement",
]
