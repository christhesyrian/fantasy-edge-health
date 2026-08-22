"""Declarative base, shared column types, and provenance mixins.

Portability
-----------
PostgreSQL is the production target. SQLite is supported so the demo runs with
no infrastructure, which means every column type used here must exist on both.
Dialect-specific types are declared with ``with_variant`` so PostgreSQL gets the
better implementation (``JSONB``, native ``UUID``) while SQLite still works.

Provenance
----------
Nearly every table carries where a row came from and when. This is not
bookkeeping for its own sake: the product's central promise is that every number
on screen can state its source and age, and that is only possible if provenance
is a column rather than an afterthought.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeEngine

# Explicit, predictable constraint names. Without this, Alembic autogenerates
# unnamed constraints that cannot be dropped in a later migration on some
# backends.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def json_column() -> TypeEngine[Any]:
    """JSON that becomes JSONB on PostgreSQL.

    Used only where preserving a provider payload is genuinely valuable -
    reproducing an ingestion, or diffing a schema change. Anything the
    application queries or filters on gets a real column.
    """
    return JSON().with_variant(JSONB, "postgresql")


def uuid_column() -> TypeEngine[Any]:
    """A UUID stored natively on PostgreSQL and as text elsewhere."""
    return String(36).with_variant(PG_UUID(as_uuid=False), "postgresql")


def utcnow() -> datetime:
    """Timezone-aware current time. Never uses naive ``datetime.now()``."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for every model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy API expects a plain dict
        dict[str, Any]: json_column(),
        datetime: DateTime(timezone=True),
    }


class TimestampMixin:
    """Row lifecycle timestamps, maintained by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProvenanceMixin:
    """Where a row came from and how current it is.

    Args:
        source: Provider key, e.g. ``"sleeper"`` or ``"nflverse"``.
        source_updated_at: When the *provider* last changed this fact, when it
            tells us. Distinct from ``ingested_at``, which is when we read it.
        ingested_at: When this system stored the row.
        observed_at: The point in time the fact describes. For a weekly injury
            report this is the report date, not the ingestion time - which is
            what makes point-in-time feature reconstruction possible and keeps
            the ML training set free of leakage.
    """

    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
