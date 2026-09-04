"""On-field production and usage tables."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fhe.db.base import (
    SEASON_LONG_WEEK,
    Base,
    ProvenanceMixin,
    TimestampMixin,
    uuid_column,
)


class PlayerWeeklyStat(Base, TimestampMixin, ProvenanceMixin):
    """One player's production in one game."""

    __tablename__ = "player_weekly_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    season_type: Mapped[str | None] = mapped_column(String(8))
    team: Mapped[str | None] = mapped_column(String(8))
    opponent: Mapped[str | None] = mapped_column(String(8))

    # Usage, which is what the health model consumes as exposure.
    carries: Mapped[float | None] = mapped_column(Float)
    targets: Mapped[float | None] = mapped_column(Float)
    receptions: Mapped[float | None] = mapped_column(Float)
    pass_attempts: Mapped[float | None] = mapped_column(Float)
    sacks_taken: Mapped[float | None] = mapped_column(Float)

    # Production.
    passing_yards: Mapped[float | None] = mapped_column(Float)
    passing_tds: Mapped[float | None] = mapped_column(Float)
    interceptions: Mapped[float | None] = mapped_column(Float)
    rushing_yards: Mapped[float | None] = mapped_column(Float)
    rushing_tds: Mapped[float | None] = mapped_column(Float)
    receiving_yards: Mapped[float | None] = mapped_column(Float)
    receiving_tds: Mapped[float | None] = mapped_column(Float)
    fumbles_lost: Mapped[float | None] = mapped_column(Float)

    # Pre-computed fantasy points for the common formats, so the war room does
    # not recompute scoring for thousands of rows on every request.
    fantasy_points_standard: Mapped[float | None] = mapped_column(Float)
    fantasy_points_half_ppr: Mapped[float | None] = mapped_column(Float)
    fantasy_points_ppr: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint(
            "player_uuid", "season", "week", "source", name="uq_weekly_stat_observation"
        ),
        Index("ix_weekly_player_season", "player_uuid", "season"),
    )


class SnapCount(Base, TimestampMixin, ProvenanceMixin):
    """Snap participation for one player in one game."""

    __tablename__ = "snap_counts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    team: Mapped[str | None] = mapped_column(String(8))

    offense_snaps: Mapped[int | None] = mapped_column(Integer)
    offense_snap_pct: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint(
            "player_uuid", "season", "week", "source", name="uq_snap_count_observation"
        ),
    )


class DepthChartSnapshot(Base, TimestampMixin, ProvenanceMixin):
    """A player's depth-chart position at a point in time."""

    __tablename__ = "depth_chart_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Not nullable: it participates in the uniqueness key. See SEASON_LONG_WEEK.
    week: Mapped[int] = mapped_column(Integer, nullable=False, default=SEASON_LONG_WEEK)
    team: Mapped[str | None] = mapped_column(String(8))
    depth_position: Mapped[str | None] = mapped_column(String(16))
    depth_order: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "player_uuid", "season", "week", "source", name="uq_depth_chart_observation"
        ),
    )


class ScheduledGame(Base, TimestampMixin, ProvenanceMixin):
    """One scheduled game, used to know who a team plays and when.

    Stored per game rather than per team-week so a row maps one-to-one onto the
    provider's record and re-ingestion converges. Callers who want "team T's
    week W opponent" derive it, which is a cheap read and keeps this table an
    honest copy of the source.

    Scores are nullable because a future season's schedule is published long
    before any of it is played, which is exactly when a draft needs it.
    """

    __tablename__ = "scheduled_games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_game_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # "REG", "POST", and the provider's other codes, kept as published.
    game_type: Mapped[str] = mapped_column(String(8), nullable=False)

    home_team: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    away_team: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    # Named for every game in both the last played season and the upcoming one,
    # which is what makes "seasons under the current coach" answerable rather
    # than assumed.
    home_coach: Mapped[str | None] = mapped_column(String(64))
    away_coach: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("provider_game_id", "source", name="uq_scheduled_game_per_provider"),
        Index("ix_scheduled_games_season_week", "season", "week"),
    )
