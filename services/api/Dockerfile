# syntax=docker/dockerfile:1
#
# API service image.
#
# Multi-stage so the runtime layer carries no build toolchain, and runs as a
# non-root user because a container that does not need root should not have it.

FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only what defines the dependency set first, so a source-only change does
# not invalidate the (slow) dependency layer.
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
COPY services/api/entrypoint.sh /usr/local/bin/fhe-entrypoint
# Explicit rather than relying on the host's file mode surviving the build
# context, which is the sort of thing that works everywhere until one checkout
# does not track the executable bit.
RUN chmod +x /usr/local/bin/fhe-entrypoint
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
EXPOSE 8000

# Liveness only: readiness is checked by the orchestrator against
# /api/v1/health/ready, which does touch the database. Reads $PORT for the same
# reason the entrypoint does, or the check would probe 8000 while the server
# listened on whatever the platform assigned and report a healthy app as dead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.environ.get('PORT','8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["fhe-entrypoint"]
