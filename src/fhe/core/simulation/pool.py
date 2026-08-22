"""Deterministic synthetic player pool.

Why synthetic rather than a shipped copy of real data:

* A bulk provider dump does not belong in git, and licensing for redistributing
  projections and ADP is not something to hand-wave.
* Tests need a pool that is *identical every run*, including its injury profiles
  and ADP dispersion. Real data changes daily and would make assertions flaky.
* A reviewer cloning the repository gets a working product immediately, with no
  credentials and no ingestion step.

The generated pool is calibrated to real positional shapes - projection curves,
replacement cliffs, ADP dispersion widening with depth, and injury prevalence -
so the draft engine is exercised under realistic conditions. It is labelled
synthetic everywhere it surfaces and is never presented as real production data.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import date
from typing import Final

from fhe.core.draft.models import DraftablePlayer
from fhe.core.health import (
    HealthInputs,
    InjuryHistoryEvent,
    WorkloadSummary,
    score_health,
)
from fhe.core.types import BodyRegion, InjuryDesignation, Position, PracticeStatus

SYNTHETIC_SOURCE: Final = "synthetic-demo"

# Season-long projected point curves per position. Each entry holds the top
# player's points, the exponential decay rate, and how deep the pool runs.
# Exponential decay reproduces the steep early cliff and the long flat tail that
# real projection sets show.
_POSITION_CURVE: Final[dict[Position, tuple[float, float, int]]] = {
    Position.QB: (395.0, 0.021, 40),
    Position.RB: (345.0, 0.030, 80),
    Position.WR: (335.0, 0.024, 100),
    Position.TE: (265.0, 0.045, 40),
    Position.K: (155.0, 0.012, 32),
    Position.DEF: (150.0, 0.020, 32),
}

# Where each position starts coming off the board, and how fast it is drafted
# thereafter, as (first_pick_offset, picks_per_step). Calibrated to the shape of
# real single-QB PPR ADP: backs and receivers open the draft, tight ends and
# quarterbacks begin in the second and third rounds, and kickers and defenses
# are the last two rounds regardless of how good they are.
_ADP_POSITION_SHAPE: Final[dict[Position, tuple[float, float]]] = {
    Position.RB: (1.0, 2.2),
    Position.WR: (2.0, 1.9),
    Position.TE: (15.0, 4.5),
    Position.QB: (25.0, 4.2),
    Position.DEF: (130.0, 2.0),
    Position.K: (150.0, 1.6),
}

# Picks a player slides per point of availability risk. The market discounts
# injured players, so a severe-risk player drifts roughly two rounds later than
# his projection alone would place him.
_ADP_RISK_DRIFT: Final = 0.35

_FIRST_NAMES: Final[tuple[str, ...]] = (
    "Marcus",
    "Devin",
    "Jalen",
    "Trey",
    "Amari",
    "Cooper",
    "Xavier",
    "Isaiah",
    "Rashad",
    "Elijah",
    "Damon",
    "Kyler",
    "Bryce",
    "Malik",
    "Terrell",
    "Deshaun",
    "Grant",
    "Nico",
    "Julian",
    "Tariq",
    "Emeka",
    "Braxton",
    "Caleb",
    "Jaxon",
    "Roman",
    "Dominic",
    "Silas",
    "Kendrick",
    "Micah",
    "Zion",
    "Landon",
    "Ezra",
    "Kai",
    "Josiah",
    "Beau",
    "Cade",
    "Rylan",
    "Trent",
    "Nolan",
    "Quinn",
)
_LAST_NAMES: Final[tuple[str, ...]] = (
    "Holloway",
    "Vance",
    "Prescott",
    "Whitfield",
    "Barron",
    "Sinclair",
    "Rhodes",
    "Alvarez",
    "Kingsley",
    "Devereaux",
    "Ashford",
    "Blackwood",
    "Cordova",
    "Trammell",
    "Okafor",
    "Sandoval",
    "Ferreira",
    "Lindqvist",
    "Marchetti",
    "Nakamura",
    "Osei",
    "Petrov",
    "Quintero",
    "Ramsey",
    "Stafford",
    "Thibodeaux",
    "Underwood",
    "Valentine",
    "Whitaker",
    "Yarborough",
    "Zamora",
    "Brantley",
    "Calloway",
    "Donnelly",
    "Eastwood",
    "Fontaine",
    "Galloway",
    "Hartman",
    "Ibarra",
    "Jennings",
)
_TEAMS: Final[tuple[str, ...]] = (
    "ARI",
    "ATL",
    "BAL",
    "BUF",
    "CAR",
    "CHI",
    "CIN",
    "CLE",
    "DAL",
    "DEN",
    "DET",
    "GB",
    "HOU",
    "IND",
    "JAX",
    "KC",
    "LAC",
    "LAR",
    "LV",
    "MIA",
    "MIN",
    "NE",
    "NO",
    "NYG",
    "NYJ",
    "PHI",
    "PIT",
    "SEA",
    "SF",
    "TB",
    "TEN",
    "WAS",
)

# Share of the pool carrying an active injury designation, matched roughly to
# what the live Sleeper payload shows in preseason.
_DESIGNATION_MIX: Final[tuple[tuple[InjuryDesignation, float], ...]] = (
    (InjuryDesignation.ACTIVE, 0.86),
    (InjuryDesignation.QUESTIONABLE, 0.07),
    (InjuryDesignation.OUT, 0.02),
    (InjuryDesignation.IR, 0.03),
    (InjuryDesignation.PUP, 0.02),
)

_COMMON_REGIONS: Final[tuple[BodyRegion, ...]] = (
    BodyRegion.KNEE,
    BodyRegion.ANKLE,
    BodyRegion.HAMSTRING,
    BodyRegion.SHOULDER,
    BodyRegion.HIP_GROIN,
    BodyRegion.CALF,
    BodyRegion.FOOT_TOE,
    BodyRegion.BACK,
    BodyRegion.HEAD,
    BodyRegion.TORSO_RIBS,
)
_REGION_RAW_TEXT: Final[dict[BodyRegion, str]] = {
    BodyRegion.KNEE: "Knee",
    BodyRegion.ANKLE: "Ankle",
    BodyRegion.HAMSTRING: "Hamstring",
    BodyRegion.SHOULDER: "Shoulder",
    BodyRegion.HIP_GROIN: "Groin",
    BodyRegion.CALF: "Calf",
    BodyRegion.FOOT_TOE: "Foot",
    BodyRegion.BACK: "Back",
    BodyRegion.HEAD: "Concussion",
    BodyRegion.TORSO_RIBS: "Ribs",
}


@dataclass(frozen=True, slots=True)
class PoolConfig:
    """Knobs for pool generation."""

    seed: int = 20260822
    season: int = 2026
    as_of: date = date(2026, 8, 22)
    include_kickers_and_defenses: bool = True


def _projection_for(position: Position, depth_index: int) -> float:
    """Exponentially decaying projection for the ``n``-th best player."""
    top, decay, _ = _POSITION_CURVE[position]
    return top * math.exp(-decay * depth_index)


def _make_name(rng: random.Random, used: set[str]) -> str:
    """Generate a unique synthetic player name."""
    for _ in range(200):
        name = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
        if name not in used:
            used.add(name)
            return name
    # Deterministic fallback keeps generation total even in a pathological case.
    suffix = len(used)
    name = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)} {suffix}"
    used.add(name)
    return name


def _choose_designation(rng: random.Random) -> InjuryDesignation:
    """Sample an injury designation from the calibrated mix."""
    roll = rng.random()
    cumulative = 0.0
    for designation, share in _DESIGNATION_MIX:
        cumulative += share
        if roll <= cumulative:
            return designation
    return InjuryDesignation.ACTIVE


def _build_history(
    rng: random.Random, season: int, position: Position, age: float
) -> tuple[InjuryHistoryEvent, ...]:
    """Generate a plausible injury history, weighted by position and age."""
    base_rate = {Position.RB: 1.5, Position.WR: 1.0, Position.TE: 1.1, Position.QB: 0.8}.get(
        position, 0.7
    )
    expected = base_rate * (1.0 + max(0.0, age - 25.0) * 0.12)
    count = min(6, int(rng.expovariate(1.0 / max(0.4, expected))))

    events: list[InjuryHistoryEvent] = []
    # A minority of players carry a genuine recurrent problem; this is what the
    # recurrence term in the health model needs to see.
    recurrent_region = rng.choice(_COMMON_REGIONS) if rng.random() < 0.28 else None

    for _ in range(count):
        region = (
            recurrent_region
            if (recurrent_region and rng.random() < 0.6)
            else rng.choice(_COMMON_REGIONS)
        )
        events.append(
            InjuryHistoryEvent(
                season=season - rng.randint(1, 3),
                week=rng.randint(1, 18),
                region=region,
                raw_descriptor=_REGION_RAW_TEXT[region],
                designation=rng.choice(
                    [
                        InjuryDesignation.QUESTIONABLE,
                        InjuryDesignation.OUT,
                        InjuryDesignation.DOUBTFUL,
                    ]
                ),
                games_missed=rng.choice([0, 0, 1, 1, 2, 3, 5]),
            )
        )
    return tuple(events)


def _build_practice(
    rng: random.Random, designation: InjuryDesignation
) -> tuple[PracticeStatus, ...]:
    """Generate a short practice-report history consistent with the designation."""
    if designation is InjuryDesignation.ACTIVE:
        return (PracticeStatus.FULL, PracticeStatus.FULL, PracticeStatus.FULL)
    if designation in {InjuryDesignation.IR, InjuryDesignation.PUP, InjuryDesignation.OUT}:
        return (PracticeStatus.DNP, PracticeStatus.DNP, PracticeStatus.DNP)
    return tuple(
        rng.choice([PracticeStatus.DNP, PracticeStatus.LIMITED, PracticeStatus.FULL])
        for _ in range(3)
    )


def generate_player_pool(config: PoolConfig | None = None) -> tuple[DraftablePlayer, ...]:
    """Build a deterministic synthetic draft pool.

    The same ``seed`` always produces byte-identical output, which is what makes
    the demo reproducible and the simulator's tests meaningful.

    Returns:
        Players ordered by ADP, ascending.
    """
    cfg = config or PoolConfig()
    rng = random.Random(cfg.seed)  # noqa: S311 - deterministic fixtures, not crypto
    used_names: set[str] = set()

    positions = [Position.QB, Position.RB, Position.WR, Position.TE]
    if cfg.include_kickers_and_defenses:
        positions += [Position.K, Position.DEF]

    raw: list[tuple[float, DraftablePlayer]] = []

    for position in positions:
        _, _, depth = _POSITION_CURVE[position]
        adp_offset, adp_slope = _ADP_POSITION_SHAPE[position]
        for index in range(depth):
            projection = _projection_for(position, index)
            age = round(rng.gauss(25.8, 2.9), 1)
            age = min(38.0, max(21.0, age))
            experience = max(0, int(age - 22 + rng.randint(-1, 1)))
            designation = _choose_designation(rng)
            region = (
                rng.choice(_COMMON_REGIONS) if designation is not InjuryDesignation.ACTIVE else None
            )

            history = _build_history(rng, cfg.season, position, age)
            games = 17 - min(6, sum(e.games_missed or 0 for e in history[:1]))
            workload = WorkloadSummary(
                season=cfg.season - 1,
                games_played=games,
                snaps_per_game=round(max(5.0, rng.gauss(45.0, 14.0)), 1),
                carries_per_game=(
                    round(max(0.0, rng.gauss(14.0 - index * 0.12, 4.0)), 1)
                    if position is Position.RB
                    else round(max(0.0, rng.gauss(1.0, 1.0)), 1)
                ),
                targets_per_game=(
                    round(max(0.0, rng.gauss(8.5 - index * 0.05, 2.5)), 1)
                    if position in {Position.WR, Position.TE}
                    else round(max(0.0, rng.gauss(2.5, 1.5)), 1)
                ),
            )

            uuid = f"syn-{position.value.lower()}-{index:03d}"
            health = score_health(
                HealthInputs(
                    player_uuid=uuid,
                    position=position,
                    as_of=cfg.as_of,
                    current_season=cfg.season,
                    designation=designation,
                    current_injury_region=region,
                    practice_statuses=_build_practice(rng, designation),
                    injury_history=history,
                    age=age,
                    years_experience=experience,
                    workload=workload,
                )
            )

            # ADP ordering key: where the market starts taking this position,
            # how fast it goes, a discount for injury risk, and noise so ADP and
            # projection disagree the way they genuinely do.
            adp_key = (
                adp_offset
                + index * adp_slope
                + health.risk_score * _ADP_RISK_DRIFT
                + rng.gauss(0.0, 4.0)
            )

            raw.append(
                (
                    adp_key,
                    DraftablePlayer(
                        player_uuid=uuid,
                        name=_make_name(rng, used_names),
                        position=position,
                        team=rng.choice(_TEAMS),
                        projected_points=round(projection, 1),
                        projection_source=SYNTHETIC_SOURCE,
                        health=health,
                        bye_week=rng.randint(5, 14),
                        age=age,
                        years_experience=experience,
                        popularity_rank=None,
                    ),
                )
            )

    raw.sort(key=lambda pair: pair[0])

    players: list[DraftablePlayer] = []
    for position_index, (_, player) in enumerate(raw):
        # ADP is the market's consensus slot, so it tracks the ordering itself
        # rather than re-randomising it.
        adp = round(position_index + 1.0, 1)
        # Dispersion widens with depth: early picks are predictable, late ones
        # are close to random across a wide band.
        stdev = round(max(2.0, min(38.0, adp * 0.30)), 1)
        players.append(
            DraftablePlayer(
                player_uuid=player.player_uuid,
                name=player.name,
                position=player.position,
                team=player.team,
                projected_points=player.projected_points,
                projection_source=SYNTHETIC_SOURCE,
                adp=adp,
                adp_stdev=stdev,
                adp_source=SYNTHETIC_SOURCE,
                health=player.health,
                bye_week=player.bye_week,
                age=player.age,
                years_experience=player.years_experience,
                popularity_rank=position_index + 1,
            )
        )
    return tuple(players)
