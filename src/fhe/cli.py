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
from pathlib import Path

from fhe import __version__
from fhe.config import Settings, get_settings
from fhe.observability import configure_logging, get_logger

# The frontend imports this file directly, so it lives inside the web app's
# source tree rather than in a data directory.
DEFAULT_PREVIEW_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "web"
    / "src"
    / "lib"
    / "preview"
    / "recorded.json"
)

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


async def _quality(settings: Settings) -> int:
    """Run every data-quality check and report."""
    from fhe.data.quality import run_quality_checks, summarise
    from fhe.db import create_engine, create_session_factory

    await _create_schema(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        results = await run_quality_checks(factory)
    finally:
        await engine.dispose()

    print(summarise(results))
    blocking = [r for r in results if r.is_blocking]
    print(
        f"\n{len(results)} checks, "
        f"{sum(1 for r in results if not r.passed)} failed, "
        f"{len(blocking)} blocking"
    )
    # A blocking failure means something downstream will be misleading, so the
    # exit code reflects it and a scheduled run can alert on it.
    return 1 if blocking else 0


async def _ml_evaluate(settings: Settings, seasons: Sequence[int], holdout: Sequence[int]) -> int:
    """Build the training set, audit it, and evaluate candidate models.

    Prints evidence. Promotes nothing: that decision is a human one taken
    against the bar in ``docs/MODEL_CARD.md``.
    """
    from fhe.db import create_engine, create_session_factory
    from fhe.ml.dataset import build_training_frame
    from fhe.ml.leakage import audit
    from fhe.ml.train import evaluate

    engine = create_engine(settings)
    factory = create_session_factory(engine)
    cutoff_week = 10
    try:
        async with factory() as session:
            rows, summary = await build_training_frame(session, seasons=list(seasons))
    finally:
        await engine.dispose()

    if not rows:
        print("No training rows. Ingest injuries and workload first.")
        return 1

    print(
        f"dataset: {summary.rows} rows, {summary.positives} positives "
        f"({summary.positive_rate:.2%}), {summary.players} players, "
        f"horizon {summary.horizon_weeks} weeks"
    )
    if not summary.is_trainable:
        print("Not enough signal to train. Ingest more seasons.")
        return 1

    print("\nleakage audit:")
    truncated = [row for row in rows if row["week"] < cutoff_week]
    findings = audit(rows, truncated_rows=truncated, cutoff_week=cutoff_week)
    for finding in findings:
        print(f"  {finding}")
    if any(not f.passed for f in findings):
        # A leaked feature makes every metric below meaningless, so there is no
        # point computing them.
        print("\nAudit failed. Refusing to evaluate on a compromised dataset.")
        return 1

    print()
    print(evaluate(rows, test_seasons=list(holdout)).summary())
    return 0


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


def _convert_fantasypros(kind: str, source_path: Path, dest_path: Path) -> int:
    """Convert a FantasyPros export into the project's import schema."""
    from fhe.data.ingest.fantasypros_csv import (
        ConversionError,
        convert_adp,
        convert_projections,
    )

    try:
        text = source_path.read_text(encoding="utf-8-sig")
    except OSError as error:
        print(f"cannot read {source_path}: {error}")
        return 1

    convert = convert_projections if kind == "projections" else convert_adp
    try:
        converted, report = convert(text)
    except ConversionError as error:
        print(f"Could not convert {source_path}:\n  {error}")
        return 1

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(converted, encoding="utf-8")
    print(report.render())
    print(f"\nwrote {dest_path}")
    if report.rows_written == 0:
        print("Nothing was written — check the column mapping above.")
        return 1
    return 0


async def _loadtest(base_url: str, concurrency: int, duration: float, draft_id: str | None) -> int:
    """Run the read-path load test against a live API."""
    import httpx

    from fhe.loadtest import run_load_test
    from fhe.loadtest.runner import default_scenarios

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        if draft_id is None:
            # A demo simulation needs no ingested data, which keeps the load
            # test runnable on a clean checkout.
            created = await client.post(
                "/api/v1/simulations",
                json={
                    "team_count": 12,
                    "user_draft_slot": 5,
                    "scoring_format": "ppr",
                    "seed": 42,
                },
            )
            created.raise_for_status()
            draft_id = str(created.json()["simulation_id"])

        board = await client.get(f"/api/v1/drafts/{draft_id}/board?depth=5")
        board.raise_for_status()
        recommendations = board.json().get("recommendations") or []
        player_uuid = recommendations[0]["player_uuid"] if recommendations else None

    result = await run_load_test(
        base_url,
        default_scenarios(draft_id, player_uuid),
        concurrency=concurrency,
        duration_seconds=duration,
    )
    print(result.render())
    return 0 if result.total_errors == 0 else 1


async def _preview_capture(destination: Path) -> int:
    """Re-record the frontend's offline preview fixtures."""
    from fhe.preview.capture import capture_fixtures, write_fixtures

    fixtures = await capture_fixtures()
    written = write_fixtures(fixtures, destination)
    print(
        f"Recorded {len(fixtures.snapshots)} board snapshots and "
        f"{len(fixtures.players)} player details "
        f"({written / 1024:.0f} KiB) to {destination}"
    )
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

    preview = subcommands.add_parser(
        "preview", help="Frontend preview fixtures recorded from the real API"
    )
    preview_sub = preview.add_subparsers(dest="preview_command", required=True)
    preview_capture = preview_sub.add_parser(
        "capture", help="Re-record apps/web preview fixtures from the demo simulator"
    )
    preview_capture.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_PREVIEW_FIXTURE_PATH,
        help="Where to write the recording",
    )

    convert = subcommands.add_parser(
        "convert", help="Convert a provider's CSV export into the import schema"
    )
    convert_sub = convert.add_subparsers(dest="convert_source", required=True)
    fp_csv = convert_sub.add_parser(
        "fantasypros", help="Convert a FantasyPros CSV export you downloaded yourself"
    )
    fp_csv.add_argument("--kind", choices=["projections", "adp"], required=True)
    fp_csv.add_argument("--in", dest="source_path", type=Path, required=True)
    fp_csv.add_argument("--out", dest="dest_path", type=Path, required=True)

    loadtest = subcommands.add_parser(
        "loadtest", help="Drive concurrent reads against a running API"
    )
    loadtest.add_argument("--base-url", default="http://127.0.0.1:8000")
    loadtest.add_argument("--concurrency", type=int, default=20)
    loadtest.add_argument("--duration", type=float, default=15.0)
    loadtest.add_argument(
        "--draft-id",
        default=None,
        help="Existing draft to read. A demo simulation is created when omitted.",
    )

    subcommands.add_parser("seed", help="Create the database schema")
    subcommands.add_parser("quality", help="Run data-quality checks")

    ml = subcommands.add_parser("ml", help="Availability model workflows")
    ml_sub = ml.add_subparsers(dest="ml_command", required=True)
    ml_eval = ml_sub.add_parser("evaluate", help="Build the dataset, audit it, and evaluate models")
    ml_eval.add_argument(
        "--seasons",
        type=_parse_seasons,
        default=list(range(2016, 2026)),
        help="Seasons to build the dataset from",
    )
    ml_eval.add_argument(
        "--holdout",
        type=_parse_seasons,
        default=[2024, 2025],
        help="Seasons held out for testing. Whole seasons only, never a date cut.",
    )

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
    if args.command == "convert":
        return _convert_fantasypros(args.kind, args.source_path, args.dest_path)
    if args.command == "loadtest":
        return asyncio.run(_loadtest(args.base_url, args.concurrency, args.duration, args.draft_id))
    if args.command == "preview":
        return asyncio.run(_preview_capture(args.out))
    if args.command == "seed":
        return asyncio.run(_seed(settings))
    if args.command == "quality":
        return asyncio.run(_quality(settings))
    if args.command == "ml":
        return asyncio.run(_ml_evaluate(settings, args.seasons, args.holdout))

    # argparse rejects an unknown command before reaching here; this exists so
    # a new subcommand added without a branch fails loudly rather than silently
    # succeeding with exit code 0.
    parser.error(f"unhandled command {args.command!r}")


if __name__ == "__main__":
    sys.exit(main())
