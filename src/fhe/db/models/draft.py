"""Leagues, drafts, picks, and rosters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fhe.db.base import Base, ProvenanceMixin, TimestampMixin, json_column, uuid_column


class FantasyLeague(Base, TimestampMixin, ProvenanceMixin):
    """A fantasy league, whether connected from a provider or configured manually."""

    __tablename__ = "fantasy_leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # NULL for a manually configured or demo league.
    provider_league_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(String(24))

    team_count: Mapped[int] = mapped_column(Integer, nullable=False)
    scoring_format: Mapped[str] = mapped_column(String(16), nullable=False)
    roster_positions: Mapped[dict[str, Any]] = mapped_column(json_column(), nullable=False)
    scoring_settings: Mapped[dict[str, Any] | None] = mapped_column(json_column())
    raw_settings: Mapped[dict[str, Any] | None] = mapped_column(json_column())

    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    drafts: Mapped[list[Draft]] = relationship(
        back_populates="league", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("provider_league_id", "source", name="uq_league_per_provider"),
    )


class Draft(Base, TimestampMixin, ProvenanceMixin):
    """A draft belonging to a league."""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("fantasy_leagues.id", ondelete="CASCADE"), nullable=False, index=True
    )

    provider_draft_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    draft_type: Mapped[str] = mapped_column(String(16), nullable=False)
    rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)

    user_draft_slot: Mapped[int | None] = mapped_column(Integer)
    slot_to_roster_id: Mapped[dict[str, Any] | None] = mapped_column(json_column())
    start_time: Mapped[datetime | None] = mapped_column()

    # Last time the poller successfully read this draft. Drives the LIVE /
    # STALE indicator in the war room.
    last_polled_at: Mapped[datetime | None] = mapped_column(index=True)
    last_pick_observed_at: Mapped[datetime | None] = mapped_column()

    league: Mapped[FantasyLeague] = relationship(back_populates="drafts")
    picks: Mapped[list[DraftPickRecord]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("provider_draft_id", "source", name="uq_draft_per_provider"),
    )


class DraftSlot(Base, TimestampMixin):
    """A seat in a draft and who occupies it."""

    __tablename__ = "draft_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    draft_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    roster_id: Mapped[int | None] = mapped_column(Integer)
    provider_user_id: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(96))
    team_name: Mapped[str | None] = mapped_column(String(96))
    is_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (UniqueConstraint("draft_id", "draft_slot", name="uq_slot_per_draft"),)


class DraftPickRecord(Base, TimestampMixin, ProvenanceMixin):
    """One selection.

    The unique constraint on ``(draft_id, pick_no)`` is the database-level
    guarantee behind the idempotency the poller relies on: even if two workers
    process the same provider response simultaneously, a pick cannot be stored
    twice. The second insert fails, and that is the correct outcome.

    A second constraint on ``(draft_id, player_uuid)`` enforces that a player is
    drafted at most once per draft.
    """

    __tablename__ = "draft_picks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    pick_no: Mapped[int] = mapped_column(Integer, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    roster_id: Mapped[int | None] = mapped_column(Integer)

    player_uuid: Mapped[str | None] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="SET NULL")
    )
    # Retained even when identity resolution fails, so a pick is never lost
    # because we could not recognise the player.
    provider_player_id: Mapped[str | None] = mapped_column(String(64))

    picked_by: Mapped[str | None] = mapped_column(String(64))
    is_keeper: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_payload: Mapped[dict[str, Any] | None] = mapped_column(json_column())

    draft: Mapped[Draft] = relationship(back_populates="picks")

    __table_args__ = (
        UniqueConstraint("draft_id", "pick_no", name="uq_pick_number_per_draft"),
        UniqueConstraint("draft_id", "player_uuid", name="uq_player_once_per_draft"),
        Index("ix_pick_draft_order", "draft_id", "pick_no"),
    )


class FantasyRoster(Base, TimestampMixin):
    """A team within a league."""

    __tablename__ = "fantasy_rosters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(
        ForeignKey("fantasy_leagues.id", ondelete="CASCADE"), nullable=False, index=True
    )

    roster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_provider_id: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(96))
    is_user: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    players: Mapped[list[RosterPlayer]] = relationship(
        back_populates="roster", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("league_id", "roster_id", name="uq_roster_per_league"),)


class RosterPlayer(Base, TimestampMixin):
    """A player on a fantasy roster."""

    __tablename__ = "roster_players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    roster_pk: Mapped[int] = mapped_column(
        ForeignKey("fantasy_rosters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    lineup_slot: Mapped[str | None] = mapped_column(String(16))
    is_starter: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    roster: Mapped[FantasyRoster] = relationship(back_populates="players")

    __table_args__ = (
        UniqueConstraint("roster_pk", "player_uuid", name="uq_player_once_per_roster"),
    )


class DraftRecommendationSnapshot(Base, TimestampMixin):
    """The board as it was shown at a specific pick.

    Persisted so a draft can be reviewed afterwards: what did the system
    actually recommend at pick 29, and why. Without this, post-draft analysis
    would have to re-derive a board from data that has since changed.
    """

    __tablename__ = "draft_recommendation_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False, index=True
    )

    pick_no: Mapped[int] = mapped_column(Integer, nullable=False)
    user_draft_slot: Mapped[int | None] = mapped_column(Integer)

    best_player_uuid: Mapped[str | None] = mapped_column(uuid_column())
    best_overall_score: Mapped[float | None] = mapped_column(Float)
    recommendations: Mapped[dict[str, Any]] = mapped_column(json_column(), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(nullable=False)
    computation_ms: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (UniqueConstraint("draft_id", "pick_no", name="uq_recommendation_per_pick"),)
