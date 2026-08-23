"""Connecting a real Sleeper draft to a war-room session."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.api.services.draft_session import DraftSessionRegistry
from fhe.api.services.league_connect import (
    DraftNotFoundError,
    EmptyPlayerPoolError,
    connect_sleeper_draft,
    league_settings_from,
    resolve_user_slot,
    scoring_format_of,
)
from fhe.config import Settings
from fhe.core.types import DraftStatus, Position, ScoringFormat
from fhe.data.providers.sleeper import SleeperDraft, SleeperLeague, SleeperPick
from fhe.db import Base, create_engine, create_session_factory
from fhe.db.models.draft import Draft, FantasyLeague

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 22, tzinfo=UTC)
LEAGUE_ID = "200000000000000001"
DRAFT_ID = "300000000000000001"
USER_ID = "100000000000000005"

ROSTER = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", *["BN"] * 6]


def make_league(**overrides: Any) -> SleeperLeague:
    """A Sleeper league payload."""
    return SleeperLeague(
        league_id=LEAGUE_ID,
        name=overrides.get("name", "Demo Dynasty"),
        season="2026",
        total_rosters=overrides.get("total_rosters", 12),
        status="pre_draft",
        sport="nfl",
        roster_positions=tuple(overrides.get("roster_positions", ROSTER)),
        scoring_settings=overrides.get("scoring_settings", {"rec": 1.0}),
        settings={},
        draft_id=DRAFT_ID,
    )


def make_draft(**overrides: Any) -> SleeperDraft:
    """A Sleeper draft payload."""
    return SleeperDraft(
        draft_id=DRAFT_ID,
        league_id=LEAGUE_ID,
        status=overrides.get("status", "drafting"),
        draft_type="snake",
        season="2026",
        settings=overrides.get("settings", {"teams": 12, "rounds": 15}),
        metadata={"scoring_type": "ppr"},
        draft_order=overrides.get("draft_order", {USER_ID: 5}),
        slot_to_roster_id={str(i): i for i in range(1, 13)},
        start_time_ms=1755900000000,
    )


class FakeSleeper:
    """A Sleeper stand-in returning canned payloads."""

    def __init__(
        self,
        league: SleeperLeague | None,
        draft: SleeperDraft | None,
        picks: tuple[SleeperPick, ...] = (),
    ) -> None:
        self._league = league
        self._draft = draft
        self._picks = picks

    async def get_league(self, league_id: str) -> SleeperLeague | None:
        return self._league

    async def get_draft(self, draft_id: str) -> SleeperDraft | None:
        return self._draft

    async def get_draft_picks(self, draft_id: str) -> tuple[SleeperPick, ...]:
        return self._picks


def sleeper_pick(pick_no: int, player_id: str) -> SleeperPick:
    """A provider pick in a 12-team draft."""
    return SleeperPick(
        draft_id=DRAFT_ID,
        pick_no=pick_no,
        round_number=(pick_no - 1) // 12 + 1,
        draft_slot=((pick_no - 1) % 12) + 1,
        player_id=player_id,
        roster_id=((pick_no - 1) % 12) + 1,
        picked_by=None,
        is_keeper=False,
        metadata={},
    )


class TestDerivation:
    @pytest.mark.parametrize(
        ("rec", "expected"),
        [
            (1.0, ScoringFormat.PPR),
            (0.5, ScoringFormat.HALF_PPR),
            (0.0, ScoringFormat.STANDARD),
        ],
    )
    def test_scoring_format_reads_actual_settings_not_a_label(
        self, rec: float, expected: ScoringFormat
    ) -> None:
        """A league calling itself PPR with 0.5 per reception is half-PPR."""
        assert scoring_format_of(make_league(scoring_settings={"rec": rec})) is expected

    def test_user_slot_comes_from_the_draft_order_map(self) -> None:
        assert resolve_user_slot(make_draft(), USER_ID) == 5

    def test_unknown_user_has_no_slot(self) -> None:
        assert resolve_user_slot(make_draft(), "someone-else") is None
        assert resolve_user_slot(make_draft(), None) is None

    def test_league_shape_and_round_count_come_from_their_own_authority(self) -> None:
        """A 15-spot roster with a 14-round draft is legal; both are honoured."""
        settings = league_settings_from(make_league(), make_draft(), user_draft_slot=5)
        assert settings.team_count == 12
        assert len(settings.roster_slots) == len(ROSTER)
        assert settings.total_rounds == 15
        assert settings.scoring_format is ScoringFormat.PPR
        assert settings.replacement_rank[Position.RB] == 29

    def test_roster_shape_falls_back_to_the_drafts_own_slot_settings(self) -> None:
        """A league can be deleted, private, or one the user is not in, while its
        draft stays readable. Verified against a real Sleeper draft whose league
        now returns 404."""
        draft = make_draft(
            settings={
                "teams": 6,
                "rounds": 15,
                "slots_qb": 1,
                "slots_rb": 2,
                "slots_wr": 2,
                "slots_te": 1,
                "slots_flex": 2,
                "slots_k": 1,
                "slots_def": 1,
                "slots_bn": 5,
            }
        )
        settings = league_settings_from(None, draft, user_draft_slot=None)

        assert settings.team_count == 6
        assert [slot.value for slot in settings.roster_slots] == [
            "QB",
            "RB",
            "RB",
            "WR",
            "WR",
            "TE",
            "FLEX",
            "FLEX",
            "K",
            "DEF",
            "BN",
            "BN",
            "BN",
            "BN",
            "BN",
        ]
        # Replacement level must scale to the smaller league, not assume twelve.
        assert settings.replacement_rank[Position.QB] == 6

    def test_a_draft_with_no_shape_at_all_is_rejected(self) -> None:
        """Without a roster from either source there is no replacement level,
        and a board built on that would be meaningless rather than merely thin."""
        from fhe.core.errors import LeagueConfigurationError

        with pytest.raises(LeagueConfigurationError, match="no roster shape"):
            league_settings_from(None, make_draft(), user_draft_slot=None)


class TestConnect:
    async def test_persists_the_league_and_draft(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        await connect_sleeper_draft(
            session_factory,
            FakeSleeper(make_league(), make_draft()),
            registry,
            league_id=LEAGUE_ID,
            draft_id=DRAFT_ID,
            user_id=USER_ID,
        )

        async with session_factory() as session:
            league = (await session.execute(select(FantasyLeague))).scalar_one()
            draft = (await session.execute(select(Draft))).scalar_one()

        assert league.provider_league_id == LEAGUE_ID
        assert league.is_demo is False
        assert league.scoring_format == "ppr"
        assert draft.provider_draft_id == DRAFT_ID
        assert draft.user_draft_slot == 5

    async def test_connecting_twice_is_idempotent(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """Reconnecting must not create a second league, draft, or session."""
        for _ in range(2):
            await connect_sleeper_draft(
                session_factory,
                FakeSleeper(make_league(), make_draft()),
                registry,
                league_id=LEAGUE_ID,
                draft_id=DRAFT_ID,
                user_id=USER_ID,
            )

        async with session_factory() as session:
            leagues = (await session.execute(select(FantasyLeague))).scalars().all()
            drafts = (await session.execute(select(Draft))).scalars().all()

        assert len(leagues) == 1
        assert len(drafts) == 1
        assert registry.count == 1

    async def test_session_is_keyed_by_the_provider_draft_id(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """So a reconnect resumes rather than starting a second poller."""
        connected, _, session = await connect_sleeper_draft(
            session_factory,
            FakeSleeper(make_league(), make_draft()),
            registry,
            league_id=LEAGUE_ID,
            draft_id=DRAFT_ID,
            user_id=USER_ID,
        )
        assert connected.session_id == DRAFT_ID
        assert session.session_id == DRAFT_ID
        assert session.is_demo is False

    async def test_connecting_mid_draft_seeds_existing_picks(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """Otherwise a mid-draft connection shows an empty board that is a lie."""
        picks = tuple(sleeper_pick(n, f"s-{n - 1}") for n in range(1, 6))
        connected, _, session = await connect_sleeper_draft(
            session_factory,
            FakeSleeper(make_league(), make_draft(), picks),
            registry,
            league_id=LEAGUE_ID,
            draft_id=DRAFT_ID,
            user_id=USER_ID,
        )

        assert connected.picks_already_made == 5
        assert session.draft_state.pick_count == 5
        # Drafted players must already be off the board.
        board = session.evaluate()
        drafted = session.draft_state.drafted_player_uuids
        assert not drafted & {r.player_uuid for r in board.recommendations}

    async def test_binding_maps_provider_ids_to_internal_uuids(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        _, binding, _ = await connect_sleeper_draft(
            session_factory,
            FakeSleeper(make_league(), make_draft()),
            registry,
            league_id=LEAGUE_ID,
            draft_id=DRAFT_ID,
            user_id=USER_ID,
        )
        assert binding.player_id_map["s-0"] == "p-000"
        assert binding.user_draft_slot == 5

    async def test_reports_pool_provenance(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        connected, _, _ = await connect_sleeper_draft(
            session_factory,
            FakeSleeper(make_league(), make_draft()),
            registry,
            league_id=LEAGUE_ID,
            draft_id=DRAFT_ID,
            user_id=USER_ID,
        )
        assert connected.provenance.player_count == 30
        assert connected.provenance.with_projection == 30
        assert connected.provenance.with_adp == 30
        # No health rows in this fixture, which is the one gap worth flagging.
        assert any("No health data" in w for w in connected.provenance.warnings)
        assert not any("No projections" in w for w in connected.provenance.warnings)

    async def test_a_completed_draft_is_connectable_but_not_followable(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """Reviewing a finished draft is useful; polling it is pointless."""
        connected, _, _ = await connect_sleeper_draft(
            session_factory,
            FakeSleeper(make_league(), make_draft(status="complete")),
            registry,
            league_id=LEAGUE_ID,
            draft_id=DRAFT_ID,
            user_id=USER_ID,
        )
        assert connected.draft_status is DraftStatus.COMPLETE
        assert connected.is_followable is False


class TestFailures:
    async def test_a_missing_league_degrades_rather_than_failing(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        """The draft is what is being followed; the league is supporting detail."""
        draft = make_draft(
            settings={
                "teams": 12,
                "rounds": 15,
                "slots_qb": 1,
                "slots_rb": 2,
                "slots_wr": 2,
                "slots_te": 1,
                "slots_flex": 1,
                "slots_bn": 6,
            }
        )
        connected, _, session = await connect_sleeper_draft(
            session_factory,
            FakeSleeper(None, draft),
            registry,
            league_id=LEAGUE_ID,
            draft_id=DRAFT_ID,
        )

        assert connected.league.team_count == 12
        assert session.league.replacement_rank[Position.RB] == 29
        # The reconstruction is recorded rather than passed off as league data.
        async with session_factory() as db:
            league_row = (await db.execute(select(FantasyLeague))).scalar_one()
        assert league_row.roster_positions["reconstructed_from_draft"] is True
        assert league_row.status == "unavailable"

    async def test_unknown_draft_is_reported_clearly(
        self, session_factory: Any, registry: DraftSessionRegistry
    ) -> None:
        with pytest.raises(DraftNotFoundError, match="draft"):
            await connect_sleeper_draft(
                session_factory,
                FakeSleeper(make_league(), None),
                registry,
                league_id=LEAGUE_ID,
                draft_id=DRAFT_ID,
            )

    async def test_an_empty_database_gives_an_actionable_error(
        self, tmp_path: Path, registry: DraftSessionRegistry
    ) -> None:
        """Not a provider failure — a different problem needing a different fix."""
        engine = create_engine(Settings(_env_file=None, data_dir=tmp_path / "empty"))
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = create_session_factory(engine)

        try:
            with pytest.raises(EmptyPlayerPoolError, match="fhe ingest players"):
                await connect_sleeper_draft(
                    factory,
                    FakeSleeper(make_league(), make_draft()),
                    registry,
                    league_id=LEAGUE_ID,
                    draft_id=DRAFT_ID,
                )
        finally:
            await engine.dispose()
