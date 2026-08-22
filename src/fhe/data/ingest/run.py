"""Ingestion run lineage.

Every job opens a run, reports what it did, and closes it. The run row survives
whatever happens to the job's own transaction, which is the whole point: a job
that fails halfway and rolls back its writes must still leave a record saying it
failed, or the pipeline's health is unknowable.

That is why the recorder uses its **own session**, separate from the session the
job writes data through.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum, unique
from time import perf_counter
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fhe.db.base import utcnow
from fhe.db.models.pipeline import DataIngestionRun
from fhe.observability import INGESTION_ROWS, get_logger

log = get_logger(__name__)

# How many rejected records to keep as examples. Enough to diagnose a pattern,
# small enough that a catastrophic run does not write a megabyte of JSON.
MAX_RETAINED_REJECTIONS = 20


@unique
class RunStatus(StrEnum):
    """Terminal state of an ingestion run."""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass
class IngestionRunRecorder:
    """Mutable counters a job updates as it works.

    Args:
        provider: Provider key, e.g. ``"sleeper"``.
        dataset: Logical dataset, e.g. ``"injuries"``.
        requested_resource: The specific thing requested, e.g. ``"injuries_2025"``.
    """

    provider: str
    dataset: str
    requested_resource: str | None = None

    rows_read: int = 0
    rows_written: int = 0
    rows_rejected: int = 0
    rows_unresolved_identity: int = 0

    source_checksum: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    run_id: int | None = None

    rejections: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def reject(self, reason: str, **context: Any) -> None:
        """Record a rejected record.

        Rejections are counted always and sampled up to
        :data:`MAX_RETAINED_REJECTIONS`, so a systematic problem is visible
        without the run row growing without bound.
        """
        self.rows_rejected += 1
        INGESTION_ROWS.labels(self.dataset, "rejected").inc()
        if len(self.rejections) < MAX_RETAINED_REJECTIONS:
            self.rejections.append({"reason": reason, **context})

    def wrote(self, count: int) -> None:
        """Record successfully written rows."""
        self.rows_written += count
        INGESTION_ROWS.labels(self.dataset, "written").inc(count)

    def read(self, count: int) -> None:
        """Record rows read from the source."""
        self.rows_read += count
        INGESTION_ROWS.labels(self.dataset, "read").inc(count)

    def unresolved_identity(self, count: int = 1) -> None:
        """Record records whose player could not be identified."""
        self.rows_unresolved_identity += count
        INGESTION_ROWS.labels(self.dataset, "unresolved_identity").inc(count)

    @property
    def rejection_rate(self) -> float:
        """Share of read rows that were rejected."""
        return self.rows_rejected / self.rows_read if self.rows_read else 0.0

    def status(self) -> RunStatus:
        """Derive the terminal status from the counters.

        A run that read nothing is **not** a success. An empty payload is
        usually a provider problem, and it is exactly the condition that would
        otherwise quietly wipe good data.
        """
        if self.rows_read == 0:
            return RunStatus.FAILED
        if self.rows_rejected:
            return RunStatus.PARTIAL
        return RunStatus.SUCCESS

    def payload(self) -> dict[str, Any]:
        """Assemble the details column."""
        payload = dict(self.details)
        if self.rejections:
            payload["rejection_samples"] = self.rejections
            payload["rejections_retained"] = len(self.rejections)
        return payload


@asynccontextmanager
async def ingestion_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    provider: str,
    dataset: str,
    requested_resource: str | None = None,
) -> AsyncIterator[IngestionRunRecorder]:
    """Open an ingestion run, and close it whatever happens.

    On an exception the run is marked ``failed`` with the error category and
    detail, and the exception is re-raised - never swallowed.

    Yields:
        The recorder the job updates as it works.
    """
    recorder = IngestionRunRecorder(
        provider=provider, dataset=dataset, requested_resource=requested_resource
    )
    started = utcnow()
    clock = perf_counter()

    async with session_factory() as session:
        record = DataIngestionRun(
            provider=provider,
            dataset=dataset,
            requested_resource=requested_resource,
            status=RunStatus.RUNNING.value,
            started_at=started,
        )
        session.add(record)
        await session.commit()
        recorder.run_id = record.id

    log.info("ingestion_started", provider=provider, dataset=dataset, run_id=recorder.run_id)

    try:
        yield recorder
    except Exception as error:
        await _close(
            session_factory,
            recorder,
            status=RunStatus.FAILED,
            duration=perf_counter() - clock,
            error_category=type(error).__name__,
            error_detail=str(error)[:2000],
        )
        log.error(
            "ingestion_failed",
            provider=provider,
            dataset=dataset,
            run_id=recorder.run_id,
            error=str(error),
        )
        raise

    status = recorder.status()
    await _close(session_factory, recorder, status=status, duration=perf_counter() - clock)
    log.info(
        "ingestion_finished",
        provider=provider,
        dataset=dataset,
        run_id=recorder.run_id,
        status=status.value,
        rows_read=recorder.rows_read,
        rows_written=recorder.rows_written,
        rows_rejected=recorder.rows_rejected,
        unresolved_identity=recorder.rows_unresolved_identity,
    )


async def _close(
    session_factory: async_sessionmaker[AsyncSession],
    recorder: IngestionRunRecorder,
    *,
    status: RunStatus,
    duration: float,
    error_category: str | None = None,
    error_detail: str | None = None,
) -> None:
    """Write the terminal state of a run."""
    if recorder.run_id is None:
        return
    async with session_factory() as session:
        await session.execute(
            update(DataIngestionRun)
            .where(DataIngestionRun.id == recorder.run_id)
            .values(
                status=status.value,
                finished_at=utcnow(),
                duration_seconds=round(duration, 3),
                rows_read=recorder.rows_read,
                rows_written=recorder.rows_written,
                rows_rejected=recorder.rows_rejected,
                rows_unresolved_identity=recorder.rows_unresolved_identity,
                source_checksum=recorder.source_checksum,
                error_category=error_category,
                error_detail=error_detail,
                details=recorder.payload(),
            )
        )
        await session.commit()
