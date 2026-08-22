"""Canonical player identity and external id crosswalk."""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fhe.db.base import Base, ProvenanceMixin, TimestampMixin, uuid_column


class Player(Base, TimestampMixin, ProvenanceMixin):
    """A single NFL player, keyed by an internal immutable identifier.

    ``player_uuid`` is derived deterministically from the strongest external
    identifier available (see :func:`fhe.data.identity.make_player_uuid`), so
    re-running ingestion never creates a duplicate and never re-keys a player.
    """

    __tablename__ = "players"

    player_uuid: Mapped[str] = mapped_column(uuid_column(), primary_key=True)

    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))

    position: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    team: Mapped[str | None] = mapped_column(String(8), index=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer)

    birth_date: Mapped[date | None] = mapped_column(Date)
    age: Mapped[float | None] = mapped_column(Float)
    years_experience: Mapped[int | None] = mapped_column(Integer)
    height_inches: Mapped[int | None] = mapped_column(Integer)
    weight_pounds: Mapped[int | None] = mapped_column(Integer)
    college: Mapped[str | None] = mapped_column(String(96))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Sleeper's popularity ordering. Useful as a weak prior when no ADP exists.
    popularity_rank: Mapped[int | None] = mapped_column(Integer, index=True)

    # How this player's identity was established, and how confident we are.
    identity_method: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    external_ids: Mapped[list[PlayerExternalId]] = relationship(
        back_populates="player", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_players_position_team", "position", "team"),
        Index("ix_players_active_position", "is_active", "position"),
    )


class PlayerExternalId(Base, TimestampMixin):
    """One provider's identifier for a player.

    Kept as rows rather than columns so a new provider needs no migration, and
    so the uniqueness of ``(system, external_id)`` is enforced by the database
    rather than by hope.
    """

    __tablename__ = "player_external_ids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_uuid: Mapped[str] = mapped_column(
        uuid_column(), ForeignKey("players.player_uuid", ondelete="CASCADE"), nullable=False
    )
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)

    player: Mapped[Player] = relationship(back_populates="external_ids")

    __table_args__ = (
        UniqueConstraint("system", "external_id", name="uq_external_id_per_system"),
        UniqueConstraint("player_uuid", "system", name="uq_one_id_per_system_per_player"),
        Index("ix_player_external_lookup", "system", "external_id"),
    )


class PlayerIdentityConflict(Base, TimestampMixin):
    """A player the resolver could not identify with enough confidence.

    Written instead of guessing. Every row here is a known unknown that a human
    can adjudicate, and the diagnostics endpoint surfaces the count so silent
    drift is impossible.
    """

    __tablename__ = "player_identity_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sleeper_id: Mapped[str | None] = mapped_column(String(64), index=True)
    observed_name: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    team: Mapped[str | None] = mapped_column(String(8))
    position: Mapped[str] = mapped_column(String(16), nullable=False)

    reason: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    candidate_ids: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)

    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    resolved_player_uuid: Mapped[str | None] = mapped_column(uuid_column())

    __table_args__ = (
        UniqueConstraint("sleeper_id", "reason", name="uq_conflict_per_player_reason"),
    )


class Team(Base, TimestampMixin):
    """An NFL team."""

    __tablename__ = "teams"

    abbreviation: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    conference: Mapped[str | None] = mapped_column(String(8))
    division: Mapped[str | None] = mapped_column(String(16))
    bye_week: Mapped[int | None] = mapped_column(Integer)
    season: Mapped[int | None] = mapped_column(Integer, index=True)


class Season(Base, TimestampMixin):
    """An NFL season and its current state."""

    __tablename__ = "seasons"

    season: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_type: Mapped[str | None] = mapped_column(String(16))
    current_week: Mapped[int | None] = mapped_column(Integer)
    start_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
