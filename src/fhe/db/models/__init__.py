"""SQLAlchemy models.

Imported as a package so Alembic's autogenerate sees every table via
``Base.metadata``. Adding a model file means adding it here.
"""

from fhe.db.models.draft import (
    Draft,
    DraftPickRecord,
    DraftRecommendationSnapshot,
    DraftSlot,
    FantasyLeague,
    FantasyRoster,
    RosterPlayer,
)
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection, FantasyRanking
from fhe.db.models.football import DepthChartSnapshot, PlayerWeeklyStat, SnapCount
from fhe.db.models.health import (
    AvailabilityPrediction,
    CurrentPlayerHealth,
    HealthScoreSnapshot,
    InjuryEvent,
    PracticeReport,
)
from fhe.db.models.pipeline import (
    DataIngestionRun,
    DataQualityResult,
    ProviderSyncState,
)
from fhe.db.models.player import (
    Player,
    PlayerExternalId,
    PlayerIdentityConflict,
    Season,
    Team,
)

__all__ = [
    "AdpSnapshot",
    "AvailabilityPrediction",
    "CurrentPlayerHealth",
    "DataIngestionRun",
    "DataQualityResult",
    "DepthChartSnapshot",
    "Draft",
    "DraftPickRecord",
    "DraftRecommendationSnapshot",
    "DraftSlot",
    "FantasyLeague",
    "FantasyProjection",
    "FantasyRanking",
    "FantasyRoster",
    "HealthScoreSnapshot",
    "InjuryEvent",
    "Player",
    "PlayerExternalId",
    "PlayerIdentityConflict",
    "PlayerWeeklyStat",
    "PracticeReport",
    "ProviderSyncState",
    "RosterPlayer",
    "Season",
    "SnapCount",
    "Team",
]
