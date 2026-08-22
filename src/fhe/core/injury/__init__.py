"""Injury text normalisation.

Providers report free text ("right Shoulder", "Not injury related - resting p",
"Knee - ACL + MCL").  This package maps that text onto a small controlled
taxonomy **without discarding the original string** - the raw value is always
persisted alongside the normalised one so a mapping bug is recoverable and
auditable.
"""

from fhe.core.injury.practice import (
    normalize_practice_status,
    practice_trajectory,
)
from fhe.core.injury.taxonomy import (
    normalize_body_region,
    normalize_body_regions,
    normalize_designation,
)

__all__ = [
    "normalize_body_region",
    "normalize_body_regions",
    "normalize_designation",
    "normalize_practice_status",
    "practice_trajectory",
]
