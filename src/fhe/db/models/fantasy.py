"""Projections, ADP, and computed rankings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fhe.db.base import (
    SEASON_LONG_WEEK,
    Base,
    ProvenanceMixin,
    TimestampMixin,
    json_column,
    uuid_column,
)


class FantasyProjection(Base, TimestampMixin, ProvenanceMixin):
    """A season or weekly point projection from one provider.

    Multiple providers can coexist for the same player and period; the ranking
    layer chooses which to use and always reports which one it used.
    """

    __tablename__ = "fantasy_projections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # ``SEASON_LONG_WEEK`` (0) means a full-season projection, which is what a
    # draft uses. Not nullable - see SEASON_LONG_WEEK for why.
    week: Mapped[int] = mapped_column(Integer, nullable=False, default=SEASON_LONG_WEEK, index=True)
    scoring_format: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    projected_points: Mapped[float] = mapped_column(Float, nullable=False)
    projected_points_low: Mapped[float | None] = mapped_column(Float)
    projected_points_high: Mapped[float | None] = mapped_column(Float)
    projected_games: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint(
            "player_uuid",
            "season",
            "week",
            "scoring_format",
            "source",
            name="uq_projection_observation",
        ),
        Index("ix_projection_season_format", "season", "scoring_format"),
    )


class AdpSnapshot(Base, TimestampMixin, ProvenanceMixin):
    """Average draft position as of a point in time.

    ADP moves daily, so this is a time series rather than a mutable value. The
    war room reads the most recent snapshot and displays its timestamp; a stale
    ADP is shown as stale rather than silently trusted.
    """

    __tablename__ = "adp_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    scoring_format: Mapped[str] = mapped_column(String(16), nullable=False)
    league_size: Mapped[int | None] = mapped_column(Integer)

    adp: Mapped[float] = mapped_column(Float, nullable=False)
    adp_stdev: Mapped[float | None] = mapped_column(Float)
    min_pick: Mapped[float | None] = mapped_column(Float)
    max_pick: Mapped[float | None] = mapped_column(Float)
    sample_size: Mapped[int | None] = mapped_column(Integer)

    snapshot_date: Mapped[datetime] = mapped_column(nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "player_uuid",
            "season",
            "scoring_format",
            "source",
            "snapshot_date",
            name="uq_adp_snapshot",
        ),
        Index("ix_adp_season_format_date", "season", "scoring_format", "snapshot_date"),
    )


class FantasyRanking(Base, TimestampMixin, ProvenanceMixin):
    """A computed, injury-adjusted ranking for a league configuration.

    Rankings are league-specific: replacement level differs between a 10-team
    single-QB league and a 12-team superflex, so a single global ranking would
    be wrong for nearly everyone.
    """

    __tablename__ = "fantasy_rankings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    scoring_format: Mapped[str] = mapped_column(String(16), nullable=False)
    league_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # Hash of the roster configuration, so two leagues with identical shape share
    # a ranking instead of recomputing it per league.
    roster_signature: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    overall_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    position_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer)

    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    value_over_replacement: Mapped[float | None] = mapped_column(Float)
    health_risk: Mapped[float | None] = mapped_column(Float)
    adp_value: Mapped[float | None] = mapped_column(Float)

    components: Mapped[dict[str, Any] | None] = mapped_column(json_column())
    computed_at: Mapped[datetime] = mapped_column(nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "player_uuid",
            "season",
            "roster_signature",
            "computed_at",
            name="uq_ranking_snapshot",
        ),
        Index("ix_ranking_lookup", "season", "roster_signature", "overall_rank"),
    )
