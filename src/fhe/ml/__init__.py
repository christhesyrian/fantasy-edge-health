"""Availability modelling.

Nothing here runs in production. A learned model is only promoted once it has
been shown, on a time-based split, to beat both the heuristic and the base rate,
and once its probabilities are calibrated. Until then
:mod:`fhe.core.health.heuristic` is what the product uses, and
``docs/MODEL_CARD.md` says so.

The order of work is deliberate: define the target, build the dataset
point-in-time, audit it for leakage, establish baselines, and only then fit
anything.
"""

from fhe.ml.dataset import (
    DEFAULT_HORIZON_WEEKS,
    FEATURE_COLUMNS,
    DatasetSummary,
    build_training_frame,
)

__all__ = [
    "DEFAULT_HORIZON_WEEKS",
    "FEATURE_COLUMNS",
    "DatasetSummary",
    "build_training_frame",
]
