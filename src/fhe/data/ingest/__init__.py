"""Ingestion jobs.

Every job follows the same contract:

* It records a row in ``data_ingestion_runs`` describing what it read, wrote,
  and rejected - success or failure.
* It never silently discards a malformed record. Rejections are counted, and a
  sample is retained for diagnosis.
* It is idempotent. Re-running a job converges rather than duplicating.
* A corrupt provider response never overwrites known-good state.
"""

from fhe.data.ingest.run import IngestionRunRecorder, RunStatus, ingestion_run

__all__ = ["IngestionRunRecorder", "RunStatus", "ingestion_run"]
