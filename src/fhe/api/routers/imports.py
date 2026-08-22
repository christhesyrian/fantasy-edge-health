"""CSV import endpoints.

The one ingestion path fed by an uploaded file, so every guard in
:mod:`fhe.data.ingest.csv_import` matters here: bounded size, bounded rows,
bounded values, and nothing in the file is ever evaluated.

Uploads are read into memory deliberately. A projections file is a few hundred
kilobytes, the size cap is enforced against the raw bytes before decoding, and
streaming to disk would add a temp-file lifecycle to manage for no benefit at
this scale.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from fhe.api.deps import SessionFactoryDep
from fhe.api.schemas import ImportResult
from fhe.core.types import ScoringFormat
from fhe.data.ingest.csv_import import (
    MAX_UPLOAD_BYTES,
    CsvImportError,
    import_adp_csv,
    import_projections_csv,
)
from fhe.data.ingest.run import IngestionRunRecorder
from fhe.observability import get_logger

router = APIRouter(prefix="/imports", tags=["imports"])
log = get_logger(__name__)


async def _decode(upload: UploadFile) -> str:
    """Read and decode an uploaded CSV, enforcing the size cap first.

    The cap is checked against the raw bytes before decoding, so an oversized
    upload cannot be expanded into memory by the decode itself. ``utf-8-sig``
    strips the byte-order mark spreadsheets add, which would otherwise corrupt
    the first column name and fail header validation for a confusing reason.
    """
    raw = await upload.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise CsvImportError(f"file is {len(raw)} bytes, above the {MAX_UPLOAD_BYTES} byte limit")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CsvImportError("file is not valid UTF-8; export it as UTF-8 CSV") from error


def _result(run: IngestionRunRecorder, dataset: str, source: str) -> ImportResult:
    """Summarise a run, including why rows were refused.

    Rejections are returned to the caller rather than only logged: someone who
    just uploaded a file needs to see which rows failed and why, not discover it
    later in a dashboard.
    """
    reasons: dict[str, int] = {}
    for rejection in run.rejections:
        reason = str(rejection.get("reason", "unknown"))
        reasons[reason] = reasons.get(reason, 0) + 1
    return ImportResult(
        dataset=dataset,
        source=source,
        status=run.status().value,
        rows_read=run.rows_read,
        rows_written=run.rows_written,
        rows_rejected=run.rows_rejected,
        rejection_reasons=reasons,
        rejection_samples=list(run.rejections),
    )


@router.post("/adp", response_model=ImportResult, summary="Import ADP from CSV")
async def import_adp(
    session_factory: SessionFactoryDep,
    file: Annotated[UploadFile, File(description="CSV matching data/schemas/README.md")],
    source: Annotated[str, Form(description="Where this data came from.")],
    season: Annotated[int, Form()],
    scoring_format: Annotated[str, Form()] = "half_ppr",
    league_size: Annotated[int | None, Form()] = None,
) -> ImportResult:
    """Import average draft position.

    ``source`` is recorded against every value and displayed beside it, so a
    number on screen can always say where it came from.
    """
    text = await _decode(file)
    run = await import_adp_csv(
        session_factory,
        text,
        source=source,
        season=season,
        scoring_format=ScoringFormat.parse(scoring_format),
        league_size=league_size,
    )
    return _result(run, "adp", source)


@router.post("/projections", response_model=ImportResult, summary="Import projections from CSV")
async def import_projections(
    session_factory: SessionFactoryDep,
    file: Annotated[UploadFile, File(description="CSV matching data/schemas/README.md")],
    source: Annotated[str, Form(description="Where this data came from.")],
    season: Annotated[int, Form()],
    scoring_format: Annotated[str, Form()] = "half_ppr",
) -> ImportResult:
    """Import fantasy point projections."""
    text = await _decode(file)
    run = await import_projections_csv(
        session_factory,
        text,
        source=source,
        season=season,
        scoring_format=ScoringFormat.parse(scoring_format),
    )
    return _result(run, "projections", source)
