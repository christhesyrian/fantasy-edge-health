"""Deterministic mock drafting.

Exists so the recommendation engine can be rehearsed and regression-tested
without a live provider, and so a reviewer can experience the product with no
account and no credentials.
"""

from fhe.core.simulation.pool import (
    SYNTHETIC_SOURCE,
    PoolConfig,
    generate_player_pool,
)
from fhe.core.simulation.simulator import MockDraftSimulator, SimulationConfig

__all__ = [
    "SYNTHETIC_SOURCE",
    "MockDraftSimulator",
    "PoolConfig",
    "SimulationConfig",
    "generate_player_pool",
]
