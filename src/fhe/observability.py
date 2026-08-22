"""Structured logging and metrics.

Logs are structured from the start rather than retrofitted: every provider call,
poll cycle, and ingestion run emits machine-parseable context. In development
they render as readable console lines; in production as JSON.

Metrics use ``prometheus_client``, which requires no paid service and no network
egress. If a metric is never scraped it costs a counter increment.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from fhe.config import Settings

# A dedicated registry keeps test runs isolated from each other and avoids the
# duplicate-timeseries errors that come from re-importing a module.
REGISTRY = CollectorRegistry()

PROVIDER_REQUESTS = Counter(
    "fhe_provider_requests_total",
    "Provider HTTP requests attempted.",
    labelnames=("provider", "operation", "outcome"),
    registry=REGISTRY,
)
PROVIDER_LATENCY = Histogram(
    "fhe_provider_request_seconds",
    "Provider HTTP request latency.",
    labelnames=("provider", "operation"),
    registry=REGISTRY,
)
DRAFT_POLLS = Counter(
    "fhe_draft_polls_total",
    "Live draft poll cycles.",
    labelnames=("outcome",),
    registry=REGISTRY,
)
DRAFT_PICKS_INGESTED = Counter(
    "fhe_draft_picks_ingested_total",
    "Draft picks observed, by how they were resolved.",
    labelnames=("outcome",),
    registry=REGISTRY,
)
RECOMMENDATION_LATENCY = Histogram(
    "fhe_recommendation_seconds",
    "Time to recompute a full draft board.",
    registry=REGISTRY,
)
INGESTION_ROWS = Counter(
    "fhe_ingestion_rows_total",
    "Rows processed by ingestion, by disposition.",
    labelnames=("dataset", "disposition"),
    registry=REGISTRY,
)
ACTIVE_STREAM_CLIENTS = Gauge(
    "fhe_active_stream_clients",
    "Currently connected server-sent-event clients.",
    registry=REGISTRY,
)

# Keys whose values must never reach a log line, whatever the caller passes.
_REDACTED_KEYS = frozenset(
    {"api_key", "anthropic_api_key", "password", "secret", "token", "authorization"}
)
_REDACTED = "[redacted]"


def _redact(
    _logger: object, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that strips credential-shaped values."""
    for key in list(event_dict):
        if key.lower() in _REDACTED_KEYS:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure structlog and the stdlib logging bridge.

    Safe to call more than once; the last call wins.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
        force=True,
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        _redact,
    ]
    if settings.log_format == "json":
        processors.append(structlog.processors.format_exc_info)
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for a module."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
