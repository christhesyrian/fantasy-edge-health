"""Availability-risk estimation.

This is **not** a medical model. It estimates *fantasy-relevant availability
risk* - the chance a player's usable games are reduced - from signals a football
data provider actually publishes. It never claims a specific injury will occur.

Two modes exist:

* :mod:`fhe.core.health.heuristic` - a transparent, fully decomposable scorer
  that works from day one and is always the fallback.
* A validated ML model (see :mod:`fhe.ml`), which is only consulted when it has
  been shown to beat the heuristic out of sample and is calibrated.
"""

from fhe.core.health.heuristic import score_health
from fhe.core.health.models import (
    HealthAssessment,
    HealthInputs,
    InjuryHistoryEvent,
    RiskComponent,
    WorkloadSummary,
)

__all__ = [
    "HealthAssessment",
    "HealthInputs",
    "InjuryHistoryEvent",
    "RiskComponent",
    "WorkloadSummary",
    "score_health",
]
