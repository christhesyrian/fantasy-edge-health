"""Injury, practice, and availability-risk tables."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fhe.db.base import Base, ProvenanceMixin, TimestampMixin, json_column, uuid_column


class InjuryEvent(Base, TimestampMixin, ProvenanceMixin):
    """One injury report observation.

    The raw provider text is stored alongside the normalised region on purpose.
    A taxonomy bug must be recoverable by re-running normalisation, which is
    impossible if the original string was thrown away.

    Uniqueness on ``(player, season, week, source)`` makes re-ingesting a season
    idempotent.
    """

    __tablename__ = "injury_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week: Mapped[int | None] = mapped_column(Integer, index=True)
    game_type: Mapped[str | None] = mapped_column(String(8))

    body_region: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    secondary_region: Mapped[str | None] = mapped_column(String(32))
    raw_primary_injury: Mapped[str | None] = mapped_column(Text)
    raw_secondary_injury: Mapped[str | None] = mapped_column(Text)

    designation: Mapped[str] = mapped_column(String(24), nullable=False)
    raw_report_status: Mapped[str | None] = mapped_column(String(64))

    games_missed: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "player_uuid", "season", "week", "source", name="uq_injury_event_observation"
        ),
        Index("ix_injury_player_season", "player_uuid", "season"),
        Index("ix_injury_region_season", "body_region", "season"),
    )


class PracticeReport(Base, TimestampMixin, ProvenanceMixin):
    """A practice participation observation, modelled separately from game status.

    Kept apart from :class:`InjuryEvent` because the two answer different
    questions: "Questionable after three full practices" and "Questionable after
    three DNPs" are very different signals.
    """

    __tablename__ = "practice_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week: Mapped[int | None] = mapped_column(Integer, index=True)
    report_date: Mapped[date | None] = mapped_column(Date)

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    raw_status: Mapped[str | None] = mapped_column(String(96))
    body_region: Mapped[str | None] = mapped_column(String(32))
    raw_injury: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("player_uuid", "season", "week", "source", name="uq_practice_observation"),
        Index("ix_practice_player_season_week", "player_uuid", "season", "week"),
    )


class CurrentPlayerHealth(Base, TimestampMixin, ProvenanceMixin):
    """The latest known health status for a player, one row per player.

    Deliberately a separate table from the event log. The war room reads this on
    every board recompute, and it must be a single indexed lookup rather than an
    aggregate over an ever-growing event history.
    """

    __tablename__ = "current_player_health"

    player_uuid: Mapped[str] = mapped_column(
        uuid_column(),
        ForeignKey("players.player_uuid", ondelete="CASCADE"),
        primary_key=True,
    )

    designation: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    raw_injury_status: Mapped[str | None] = mapped_column(String(64))
    body_region: Mapped[str | None] = mapped_column(String(32))
    raw_body_part: Mapped[str | None] = mapped_column(Text)
    injury_notes: Mapped[str | None] = mapped_column(Text)
    injury_start_date: Mapped[date | None] = mapped_column(Date)

    practice_status: Mapped[str | None] = mapped_column(String(16))
    practice_trajectory: Mapped[str | None] = mapped_column(String(24))

    depth_chart_position: Mapped[str | None] = mapped_column(String(16))
    depth_chart_order: Mapped[int | None] = mapped_column(Integer)


class HealthScoreSnapshot(Base, TimestampMixin, ProvenanceMixin):
    """A computed availability-risk score, with its component breakdown.

    The components are persisted rather than recomputed so a score shown during
    a draft can be explained later exactly as it was displayed, even after the
    model version changes.
    """

    __tablename__ = "health_score_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    model_version: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    raw_score: Mapped[float] = mapped_column(Float, nullable=False)
    availability_estimate: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    practice_trajectory: Mapped[str | None] = mapped_column(String(24))

    components: Mapped[dict[str, Any]] = mapped_column(json_column(), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("player_uuid", "model_version", "computed_at", name="uq_health_snapshot"),
        Index("ix_health_player_computed", "player_uuid", "computed_at"),
    )


class AvailabilityPrediction(Base, TimestampMixin, ProvenanceMixin):
    """An ML model's calibrated availability prediction.

    Separate from :class:`HealthScoreSnapshot` because the two have different
    trust levels and different promotion rules: the heuristic always runs, while
    a model prediction is only written once that model has been validated. The
    horizon is explicit so a prediction can never be read as a different claim
    than the one it was trained for.
    """

    __tablename__ = "availability_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )

    model_version: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    horizon_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    probability_unavailable: Mapped[float] = mapped_column(Float, nullable=False)
    calibrated: Mapped[bool] = mapped_column(nullable=False, default=False)

    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    week: Mapped[int | None] = mapped_column(Integer)
    features: Mapped[dict[str, Any] | None] = mapped_column(json_column())

    __table_args__ = (
        UniqueConstraint(
            "player_uuid",
            "model_version",
            "season",
            "week",
            "horizon_weeks",
            name="uq_availability_prediction",
        ),
    )
