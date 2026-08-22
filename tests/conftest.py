"""Shared test fixtures."""

from __future__ import annotations

from datetime import date

import pytest

from fhe.core.draft.models import DraftablePlayer
from fhe.core.draft.vorp import ReplacementBaseline, compute_replacement_baseline
from fhe.core.health import HealthInputs, score_health
from fhe.core.league import LeagueSettings
from fhe.core.simulation.pool import PoolConfig, generate_player_pool
from fhe.core.types import InjuryDesignation, Position, ScoringFormat

STANDARD_ROSTER = [
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "K",
    "DEF",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
    "BN",
]


@pytest.fixture
def league() -> LeagueSettings:
    """A conventional 12-team PPR league with the user picking fifth."""
    return LeagueSettings.from_tokens(
        team_count=12,
        roster_position_tokens=STANDARD_ROSTER,
        scoring_format=ScoringFormat.PPR,
        user_draft_slot=5,
    )


@pytest.fixture
def superflex_league() -> LeagueSettings:
    """A 12-team superflex league."""
    return LeagueSettings.from_tokens(
        team_count=12,
        roster_position_tokens=[
            "QB",
            "RB",
            "RB",
            "WR",
            "WR",
            "TE",
            "FLEX",
            "SUPER_FLEX",
            "BN",
            "BN",
            "BN",
            "BN",
        ],
        scoring_format=ScoringFormat.HALF_PPR,
        user_draft_slot=1,
    )


@pytest.fixture(scope="session")
def player_pool() -> tuple[DraftablePlayer, ...]:
    """The deterministic synthetic pool. Session-scoped: generation is not free."""
    return generate_player_pool(PoolConfig(seed=20260822))


@pytest.fixture
def baseline(
    player_pool: tuple[DraftablePlayer, ...], league: LeagueSettings
) -> ReplacementBaseline:
    """Replacement baseline for the standard league over the synthetic pool."""
    return compute_replacement_baseline(player_pool, league)


def make_player(
    uuid: str,
    position: Position,
    *,
    projected_points: float | None = 200.0,
    adp: float | None = 50.0,
    adp_stdev: float | None = None,
    risk: InjuryDesignation = InjuryDesignation.ACTIVE,
    age: float | None = 26.0,
    years_experience: int | None = 4,
    bye_week: int | None = None,
    name: str | None = None,
) -> DraftablePlayer:
    """Build a player with explicit, readable attributes for a focused test."""
    health = score_health(
        HealthInputs(
            player_uuid=uuid,
            position=position,
            as_of=date(2026, 8, 22),
            current_season=2026,
            designation=risk,
            age=age,
            years_experience=years_experience,
        )
    )
    return DraftablePlayer(
        player_uuid=uuid,
        name=name or f"Player {uuid}",
        position=position,
        team="TST",
        projected_points=projected_points,
        projection_source="test",
        adp=adp,
        adp_stdev=adp_stdev,
        adp_source="test",
        health=health,
        bye_week=bye_week,
        age=age,
        years_experience=years_experience,
    )
