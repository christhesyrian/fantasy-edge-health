"""Command line interface.

Thin by design: every command is a few lines of argument handling wrapped around
a function that is independently tested. Nothing here contains logic that could
only be exercised by running the CLI.

Uses ``argparse`` rather than a framework because the surface is small and the
dependency is not worth carrying into the production image.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from fhe import __version__
from fhe.config import Settings, get_settings
from fhe.observability import configure_logging, get_logger

log = get_logger(__name__)

DEFAULT_BACKFILL_SEASONS = (2023, 2024, 2025)


def _parse_seasons(raw: str) -> list[int]:
    """Parse a comma-separated season list."""
    seasons: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            raise argparse.ArgumentTypeError(f"not a season: {token!r}")
        seasons.append(int(token))
    if not seasons:
        raise argparse.ArgumentTypeError("no seasons supplied")
    return seasons


async def _create_schema(settings: Settings) -> None:
    """Create every table directly.

    Used by ``seed`` for a throwaway local database. A real deployment uses
    Alembic; this exists so the demo does not require a migration step.
    """
    import fhe.db.models  # noqa: F401  -- registers metadata
    from fhe.db import Base, create_engine

    engine = create_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def _ingest_players(settings: Settings, *, force: bool) -> int:
    """Sync the Sleeper player universe."""
    import polars as pl

    from fhe.data.identity import NflversePlayerIndex
    from fhe.data.ingest.crosswalk import load_crosswalk
    from fhe.data.ingest.sleeper_players import sync_sleeper_players
    from fhe.data.providers.nflverse import NflverseProvider
    from fhe.data.providers.sleeper import SleeperProvider
    from fhe.db import create_engine, create_session_factory

    await _create_schema(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    try:
        crosswalk = await load_crosswalk(settings)
        async with NflverseProvider(settings) as nflverse:
            players: pl.DataFrame = await nflverse.get_players()
        index = NflversePlayerIndex.build(players.to_dicts())

        async with SleeperProvider(settings) as sleeper:
            run = await sync_sleeper_players(
                factory,
                sleeper=sleeper,
                crosswalk=crosswalk,
                nflverse_index=index,
                force_refresh=force,
            )
    finally:
        await engine.dispose()

    print(
        f"players: {run.status().value} — read {run.rows_read}, "
        f"wrote {run.rows_written}, rejected {run.rows_rejected}, "
        f"unresolved {run.rows_unresolved_identity}"
    )
    return 0 if run.rows_written else 1


async def _ingest_injuries(settings: Settings, seasons: Sequence[int]) -> int:
    """Backfill injury history for the given seasons."""
    from fhe.data.ingest.nflverse_injuries import ingest_injuries_for_season
    from fhe.data.providers.nflverse import NflverseProvider
    from fhe.db import create_engine, create_session_factory

    await _create_schema(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    failures = 0

    try:
        async with NflverseProvider(settings) as nflverse:
            for season in seasons:
                run = await ingest_injuries_for_season(factory, nflverse, season)
                print(
                    f"injuries {season}: {run.status().value} — read {run.rows_read}, "
                    f"wrote {run.rows_written}, rejected {run.rows_rejected}, "
                    f"unresolved {run.rows_unresolved_identity}"
                )
                if not run.rows_written:
                    failures += 1
    finally:
        await engine.dispose()

    return 1 if failures else 0


async def _ingest_workload(settings: Settings, seasons: Sequence[int]) -> int:
    """Backfill weekly production and snap counts."""
    from fhe.data.ingest.nflverse_workload import ingest_workload_for_season
    from fhe.data.providers.nflverse import NflverseProvider
    from fhe.db import create_engine, create_session_factory

    await _create_schema(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    failures = 0

    try:
        async with NflverseProvider(settings) as nflverse:
            for season in seasons:
                stats, snaps = await ingest_workload_for_season(factory, nflverse, season)
                for label, run in (("weekly stats", stats), ("snap counts", snaps)):
                    print(
                        f"{label} {season}: {run.status().value} — read {run.rows_read}, "
                        f"wrote {run.rows_written}, rejected {run.rows_rejected}, "
                        f"unresolved {run.rows_unresolved_identity}"
                    )
                    if not run.rows_written:
                        failures += 1
    finally:
        await engine.dispose()

    return 1 if failures else 0


def _simulate(seed: int, rounds: int, slot: int, teams: int) -> int:
    """Run a headless mock draft and print the resulting board.

    Exists so the recommendation engine can be exercised, profiled, and eyeballed
    without a browser or a database.
    """
    from fhe.core.draft import compute_replacement_baseline, evaluate_draft
    from fhe.core.league import LeagueSettings
    from fhe.core.simulation import (
        MockDraftSimulator,
        SimulationConfig,
        generate_player_pool,
    )

    roster = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", *["BN"] * 6]
    league = LeagueSettings.from_tokens(
        team_count=teams,
        roster_position_tokens=roster,
        user_draft_slot=min(slot, teams),
        rounds=rounds,
    )
    pool = generate_player_pool()
    baseline = compute_replacement_baseline(pool, league)
    simulator = MockDraftSimulator(league, pool, config=SimulationConfig(seed=seed))

    simulator.advance_to_user_turn()
    board = evaluate_draft(
        simulator.state, pool, user_draft_slot=league.user_draft_slot, baseline=baseline
    )

    best = board.best_pick
    print(f"seed {seed} · pick {board.current_pick} · {teams}-team")
    if best is None:
        print("no players available")
        return 1

    print(f"\nBEST PICK: {best.name}  {best.position} {best.team or 'FA'}")
    print(f"  score {best.overall_score}  rank {best.model_rank}  {best.recommendation}")
    for component in best.components:
        print(f"    {component.points:+7.2f}  {component.label}: {component.detail}")

    print("\nTOP 10")
    for row in board.recommendations[:10]:
        print(
            f"  {row.model_rank:>3}  {row.name:<24} {row.position:<4} "
            f"score {row.overall_score:>6.1f}  adp {row.market_adp or 0:>5.0f}  "
            f"risk {row.health_risk or 0:>3.0f}  {row.recommendation}"
        )

    if board.alerts:
        print("\nALERTS")
        for alert in board.alerts:
            print(f"  [{alert.level.value}] {alert.message}")
    return 0


async def _seed(settings: Settings) -> int:
    """Create the schema so the API can start against a fresh database."""
    await _create_schema(settings)
    print(f"schema created at {settings.sqlalchemy_url}")
    for warning in settings.storage_warnings():
        print(f"  note: {warning}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Assemble the argument parser."""
    parser = argparse.ArgumentParser(
        prog="fhe",
        description="Fantasy Health Edge — injury-adjusted draft intelligence.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    ingest = subcommands.add_parser("ingest", help="Load data from a provider")
    ingest_sub = ingest.add_subparsers(dest="dataset", required=True)

    players = ingest_sub.add_parser("players", help="Sync the Sleeper player universe")
    players.add_argument(
        "--force",
        action="store_true",
        help="Bypass the cached player payload. Use sparingly; the provider asks "
        "for at most one fetch per day.",
    )

    injuries = ingest_sub.add_parser("injuries", help="Backfill nflverse injury history")
    injuries.add_argument(
        "--seasons",
        type=_parse_seasons,
        default=list(DEFAULT_BACKFILL_SEASONS),
        help="Comma-separated seasons, e.g. 2023,2024,2025",
    )

    workload = ingest_sub.add_parser("workload", help="Backfill weekly stats and snap counts")
    workload.add_argument(
        "--seasons",
        type=_parse_seasons,
        default=list(DEFAULT_BACKFILL_SEASONS),
        help="Comma-separated seasons, e.g. 2024,2025",
    )

    simulate = subcommands.add_parser("simulate", help="Run a headless mock draft")
    simulate.add_argument("--seed", type=int, default=42)
    simulate.add_argument("--teams", type=int, default=12)
    simulate.add_argument("--slot", type=int, default=5)
    simulate.add_argument("--rounds", type=int, default=15)

    subcommands.add_parser("seed", help="Create the database schema")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings)

    if args.command == "ingest":
        if args.dataset == "players":
            return asyncio.run(_ingest_players(settings, force=args.force))
        if args.dataset == "workload":
            return asyncio.run(_ingest_workload(settings, args.seasons))
        return asyncio.run(_ingest_injuries(settings, args.seasons))
    if args.command == "simulate":
        return _simulate(args.seed, args.rounds, args.slot, args.teams)
    if args.command == "seed":
        return asyncio.run(_seed(settings))

    # argparse rejects an unknown command before reaching here; this exists so
    # a new subcommand added without a branch fails loudly rather than silently
    # succeeding with exit code 0.
    parser.error(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
