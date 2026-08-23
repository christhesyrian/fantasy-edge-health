# syntax=docker/dockerfile:1
#
# Worker image. Shares the application package with the API but runs the
# ingestion and draft-polling entrypoints instead of a web server.

FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ---------------------------------------------------------------------------

FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    FHE_LOG_FORMAT=json

RUN useradd --create-home --uid 10001 fhe

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=fhe:fhe alembic.ini ./
COPY --chown=fhe:fhe alembic ./alembic
COPY --chown=fhe:fhe data/fixtures ./data/fixtures

# Create the cache directory owned by the runtime user *before* the volume is
# mounted over it. Docker seeds an empty named volume from whatever the image
# has at that path, ownership included; with no directory here it creates the
# mount point as root instead, and a non-root process cannot write to it.
# Without this, ingestion dies on `PermissionError: data/cache/nflverse`.
RUN mkdir -p /app/data/cache && chown -R fhe:fhe /app/data

USER fhe

CMD ["fhe", "ingest", "--help"]
