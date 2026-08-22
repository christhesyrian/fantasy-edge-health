"""Data-engineering tables: ingestion lineage, quality, and sync state.

These exist so the answer to "is this number trustworthy?" is a query rather
than a guess. Every ingestion writes a run row, every quality check writes a
result, and every provider records where it last got to.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from fhe.db.base import Base, TimestampMixin, json_column


class DataIngestionRun(Base, TimestampMixin):
    """One execution of one ingestion job.

    Rejected rows are counted separately from processed rows because a run that
    silently drops 40% of its input must never look like a success.
    """

    __tablename__ = "data_ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dataset: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    requested_resource: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column()
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    rows_read: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_unresolved_identity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    error_category: Mapped[str | None] = mapped_column(String(48))
    error_detail: Mapped[str | None] = mapped_column(Text)
    # Checksum of the source payload, so a re-run can detect an unchanged source.
    source_checksum: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any] | None] = mapped_column(json_column())

    __table_args__ = (
        Index("ix_ingestion_provider_dataset_started", "provider", "dataset", "started_at"),
    )


class DataQualityResult(Base, TimestampMixin):
    """The outcome of one automated data-quality check."""

    __tablename__ = "data_quality_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    ingestion_run_id: Mapped[int | None] = mapped_column(Integer, index=True)
    check_name: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    dataset: Mapped[str] = mapped_column(String(96), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    observed_value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    failing_row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    sample_failures: Mapped[dict[str, Any] | None] = mapped_column(json_column())

    checked_at: Mapped[datetime] = mapped_column(nullable=False, index=True)


class ProviderSyncState(Base, TimestampMixin):
    """Where each provider/dataset pair last got to.

    Drives both incremental ingestion and the freshness indicator the war room
    shows next to every metric.
    """

    __tablename__ = "provider_sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(96), nullable=False)

    last_success_at: Mapped[datetime | None] = mapped_column(index=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column()
    last_failure_at: Mapped[datetime | None] = mapped_column()
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Opaque provider cursor: a season, a release timestamp, an etag.
    cursor: Mapped[str | None] = mapped_column(String(128))
    # Backoff state, so a provider that is failing is not hammered on restart.
    next_attempt_after: Mapped[datetime | None] = mapped_column()

    __table_args__ = (UniqueConstraint("provider", "dataset", name="uq_sync_state_per_dataset"),)
