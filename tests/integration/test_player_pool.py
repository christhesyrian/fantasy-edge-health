"""Building the engine's player pool from curated database rows.

The step that makes a live draft possible: the engine must not be able to tell
whether it is reasoning about real data or the demo pool.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.api.services.player_pool import load_player_pool
from fhe.config import Settings
from fhe.core.types import BodyRegion, InjuryDesignation, Position, ScoringFormat
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.base import SEASON_LONG_WEEK
from fhe.db.models.fantasy import AdpSnapshot, FantasyProjection
from fhe.db.models.health import CurrentPlayerHealth, InjuryEvent
from fhe.db.models.player import Player

pytestmark = pytest.mark.integration

SEASON = 2026
AS_OF = date(2026, 8, 22)
NOW = datetime(2026, 8, 22, tzinfo=UTC)


def player(uuid: str, name: str, position: str, **kwargs: Any) -> Player:
    """A persisted player row."""
    return Player(
        player_uuid=uuid,
        full_name=name,
        normalized_name=name.lower().replace(" ", ""),
        position=position,
        team=kwargs.get("team", "SEA"),
        age=kwargs.get("age", 26.0),
        years_experience=kwargs.get("years_experience", 4),
        is_active=True,
        popularity_rank=kwargs.get("popularity_rank", 1),
        identity_method="DIRECT_GSIS",
        identity_confidence=1.0,
        source="test",
    )


@pytest.fixture
async def session_factory(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine(Settings(_env_file=None, data_dir=tmp_path))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def seeded(session_factory: Any) -> Any:
    """A small but complete slice: projections, ADP, health, injury history."""
    async with session_factory() as session:
        session.add_all(
            [
                player("u-elite", "Elite Back", "RB", popularity_rank=1),
                player("u-hurt", "Hurt Receiver", "WR", popularity_rank=2, age=31.0),
                player("u-bare", "Bare Player", "TE", popularity_rank=3),
            ]
        )
        session.add_all(
            [
                FantasyProjection(
                    player_uuid="u-elite",
                    season=SEASON,
                    week=SEASON_LONG_WEEK,
                    scoring_format="ppr",
                    projected_points=310.5,
                    source="my_projections",
                    ingested_at=NOW,
                    observed_at=NOW,
                ),
                FantasyProjection(
                    player_uuid="u-hurt",
                    season=SEASON,
                    week=SEASON_LONG_WEEK,
                    scoring_format="ppr",
                    projected_points=240.0,
                    source="my_projections",
                    ingested_at=NOW,
                    observed_at=NOW,
                ),
            ]
        )
        session.add_all(
            [
                AdpSnapshot(
                    player_uuid="u-elite",
                    season=SEASON,
                    scoring_format="ppr",
                    adp=3.2,
                    adp_stdev=1.1,
                    snapshot_date=NOW,
                    source="my_adp",
                    ingested_at=NOW,
                    observed_at=NOW,
                ),
                AdpSnapshot(
                    player_uuid="u-hurt",
                    season=SEASON,
                    scoring_format="ppr",
                    adp=28.0,
                    snapshot_date=NOW,
                    source="my_adp",
                    ingested_at=NOW,
                    observed_at=NOW,
                ),
            ]
        )
        session.add(
            CurrentPlayerHealth(
                player_uuid="u-hurt",
                designation=InjuryDesignation.IR.value,
                body_region=BodyRegion.KNEE.value,
                raw_body_part="Knee - ACL",
                practice_status="DNP",
                source="sleeper",
                ingested_at=NOW,
                observed_at=NOW,
            )
        )
        session.add_all(
            [
                InjuryEvent(
                    player_uuid="u-hurt",
                    season=SEASON - 1,
                    week=4,
                    body_region=BodyRegion.HAMSTRING.value,
                    raw_primary_injury="right Hamstring",
                    designation=InjuryDesignation.OUT.value,
                    games_missed=2,
                    source="nflverse",
                    ingested_at=NOW,
                    observed_at=NOW,
                ),
                InjuryEvent(
                    player_uuid="u-hurt",
                    season=SEASON - 1,
                    week=9,
                    body_region=BodyRegion.HAMSTRING.value,
                    raw_primary_injury="Hamstring",
                    designation=InjuryDesignation.QUESTIONABLE.value,
                    source="nflverse",
                    ingested_at=NOW,
                    observed_at=NOW,
                ),
            ]
        )
        await session.commit()
    return session_factory


async def load(session_factory: Any) -> Any:
    """Load the pool with the standard fixture parameters."""
    async with session_factory() as session:
        return await load_player_pool(
            session, season=SEASON, scoring_format=ScoringFormat.PPR, as_of=AS_OF
        )


class TestPoolAssembly:
    async def test_joins_projection_adp_and_health(self, seeded: Any) -> None:
        pool, _ = await load(seeded)
        by_uuid = {p.player_uuid: p for p in pool}

        elite = by_uuid["u-elite"]
        assert elite.projected_points == pytest.approx(310.5)
        assert elite.projection_source == "my_projections"
        assert elite.adp == pytest.approx(3.2)
        assert elite.adp_stdev == pytest.approx(1.1)
        assert elite.adp_source == "my_adp"

    async def test_health_is_computed_from_stored_facts(self, seeded: Any) -> None:
        pool, _ = await load(seeded)
        hurt = next(p for p in pool if p.player_uuid == "u-hurt")

        assert hurt.health is not None
        assert hurt.health.risk_band == "SEVERE"
        assert hurt.health.risk_score >= 70
        # The IR designation must be the dominant contribution.
        assert any(c.name == "current_designation" for c in hurt.health.components)

    async def test_recurrent_injuries_reach_the_health_model(self, seeded: Any) -> None:
        """Two hamstring events must be visible as recurrence, not just count."""
        pool, _ = await load(seeded)
        hurt = next(p for p in pool if p.player_uuid == "u-hurt")

        assert len(hurt.injury_history) == 2
        assert hurt.health is not None
        assert any(c.name == "recurrent_injury" for c in hurt.health.components)

    async def test_raw_provider_text_survives_into_the_pool(self, seeded: Any) -> None:
        pool, _ = await load(seeded)
        hurt = next(p for p in pool if p.player_uuid == "u-hurt")
        descriptors = {event.raw_descriptor for event in hurt.injury_history}

        assert "right Hamstring" in descriptors

    async def test_a_player_with_no_data_still_appears(self, seeded: Any) -> None:
        """Missing projections must not delete a player from the board."""
        pool, _ = await load(seeded)
        bare = next(p for p in pool if p.player_uuid == "u-bare")

        assert bare.projected_points is None
        assert bare.adp is None
        assert bare.health is not None
        # Unmeasured, not safe: the score is low but confidence is too.
        assert bare.health.confidence < 0.6

    async def test_pool_is_ordered_by_market_popularity(self, seeded: Any) -> None:
        pool, _ = await load(seeded)
        assert [p.player_uuid for p in pool] == ["u-elite", "u-hurt", "u-bare"]

    async def test_scoring_format_selects_the_right_values(self, seeded: Any) -> None:
        """A half-PPR league must not silently pick up PPR projections."""
        async with seeded() as session:
            pool, provenance = await load_player_pool(
                session,
                season=SEASON,
                scoring_format=ScoringFormat.HALF_PPR,
                as_of=AS_OF,
            )
        assert provenance.with_projection == 0
        assert all(p.projected_points is None for p in pool)


class TestProvenance:
    async def test_reports_completeness_and_sources(self, seeded: Any) -> None:
        _, provenance = await load(seeded)

        assert provenance.player_count == 3
        assert provenance.with_projection == 2
        assert provenance.with_adp == 2
        assert provenance.with_health == 1
        assert provenance.projection_sources == ("my_projections",)
        assert provenance.adp_sources == ("my_adp",)

    async def test_warns_when_projections_are_missing(self, session_factory: Any) -> None:
        """A board ranking on ADP alone must say so rather than look complete."""
        async with session_factory() as session:
            session.add(player("u-1", "Only Player", "RB"))
            await session.commit()

        _, provenance = await load(session_factory)
        assert not provenance.has_projections
        assert any("No projections" in w for w in provenance.warnings)

    async def test_low_health_coverage_is_not_a_warning(self, seeded: Any) -> None:
        """A healthy player has no health row by design, so a coverage ratio
        would warn on every normal database. Only zero rows is informative."""
        _, provenance = await load(seeded)
        assert provenance.with_health == 1
        assert not any("No health data" in w for w in provenance.warnings)

    async def test_warns_when_the_database_is_empty(self, session_factory: Any) -> None:
        pool, provenance = await load(session_factory)

        assert pool == ()
        assert provenance.player_count == 0
        assert any("fhe ingest players" in w for w in provenance.warnings)


@pytest.fixture
async def deep_pool(session_factory: Any) -> Any:
    """A pool deep enough for replacement level to mean something.

    A three-player pool cannot exercise VORP: with one running back on the
    board, that back *is* replacement level and his value over replacement is
    zero by definition.

    ADP is assigned by overall projection order rather than per position, which
    is how a real market behaves. Assigning it per position makes every player
    of the later position look like a bargain, which is an artefact of the
    fixture rather than a property of the engine.
    """
    specs: list[tuple[str, str, float]] = []
    for position, count, top_points in (
        ("RB", 40, 320.0),
        ("WR", 40, 300.0),
        ("TE", 20, 250.0),
    ):
        for index in range(count):
            specs.append((f"{position.lower()}-{index:03d}", position, top_points - index * 4.0))
    specs.sort(key=lambda spec: spec[2], reverse=True)

    async with session_factory() as session:
        for rank, (uuid, position, points) in enumerate(specs, start=1):
            session.add(player(uuid, f"{position} {uuid}", position, popularity_rank=rank))
            session.add(
                FantasyProjection(
                    player_uuid=uuid,
                    season=SEASON,
                    week=SEASON_LONG_WEEK,
                    scoring_format="ppr",
                    projected_points=points,
                    source="my_projections",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
            session.add(
                AdpSnapshot(
                    player_uuid=uuid,
                    season=SEASON,
                    scoring_format="ppr",
                    adp=float(rank),
                    snapshot_date=NOW,
                    source="my_adp",
                    ingested_at=NOW,
                    observed_at=NOW,
                )
            )
        await session.commit()
    return session_factory


class TestEngineCompatibility:
    async def test_the_pool_drives_the_real_engine(self, deep_pool: Any) -> None:
        """The whole point: a DB-backed pool is indistinguishable to the engine."""
        from fhe.core.draft import compute_replacement_baseline, evaluate_draft
        from fhe.core.draft.state import DraftState
        from fhe.core.league import LeagueSettings

        pool, provenance = await load(deep_pool)
        assert provenance.with_projection == 100

        league = LeagueSettings.from_tokens(
            team_count=12,
            roster_position_tokens=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"],
            scoring_format=ScoringFormat.PPR,
            user_draft_slot=1,
        )
        board = evaluate_draft(
            DraftState(league),
            pool,
            user_draft_slot=1,
            baseline=compute_replacement_baseline(pool, league),
        )

        assert board.best_pick is not None

        # Assert the invariant, not a specific player. The fixture's position
        # curves are parallel, so the top back and top receiver have identical
        # value over replacement and the winner is decided by an ADP tiebreak —
        # naming one of them would test the fixture rather than the engine.
        best_vorp = max(r.vorp for r in board.recommendations if r.vorp is not None)
        assert board.best_pick.vorp == pytest.approx(best_vorp)
        assert best_vorp > 0

        # And the top of the board must be genuinely elite, not a deep bench
        # player floated up by a secondary term.
        assert board.best_pick.player_uuid.endswith("-000")

        total = round(sum(c.points for c in board.best_pick.components), 2)
        assert total == pytest.approx(board.best_pick.overall_score, abs=0.01)

    async def test_without_projections_the_board_falls_back_to_market_order(
        self, session_factory: Any
    ) -> None:
        """Before any projection import, ADP is the only signal — and the
        provenance warnings say exactly that."""
        from fhe.core.draft import compute_replacement_baseline, evaluate_draft
        from fhe.core.draft.state import DraftState
        from fhe.core.league import LeagueSettings

        async with session_factory() as session:
            for index in range(20):
                uuid = f"rb-{index:03d}"
                session.add(player(uuid, f"Back {index}", "RB", popularity_rank=index + 1))
                session.add(
                    AdpSnapshot(
                        player_uuid=uuid,
                        season=SEASON,
                        scoring_format="ppr",
                        adp=float(index + 1),
                        snapshot_date=NOW,
                        source="my_adp",
                        ingested_at=NOW,
                        observed_at=NOW,
                    )
                )
            await session.commit()

        pool, provenance = await load(session_factory)
        assert not provenance.has_projections
        assert any("market ADP" in w for w in provenance.warnings)

        league = LeagueSettings.from_tokens(
            team_count=12,
            roster_position_tokens=["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "BN"],
            scoring_format=ScoringFormat.PPR,
            user_draft_slot=1,
        )
        board = evaluate_draft(
            DraftState(league),
            pool,
            user_draft_slot=1,
            baseline=compute_replacement_baseline(pool, league),
        )
        # Still a usable board, and still fully decomposable.
        assert board.best_pick is not None
        assert board.recommendations
        for rec in board.recommendations[:5]:
            total = round(sum(c.points for c in rec.components), 2)
            assert total == pytest.approx(rec.overall_score, abs=0.01)

    async def test_positions_parse_into_domain_enums(self, seeded: Any) -> None:
        pool, _ = await load(seeded)
        assert {p.position for p in pool} == {Position.RB, Position.WR, Position.TE}
