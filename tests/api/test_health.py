"""Health, readiness, and metrics endpoints."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


class TestLiveness:
    async def test_reports_ok(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"]

    async def test_surfaces_active_degradations(self, client: httpx.AsyncClient) -> None:
        """A SQLite/in-process deployment must never look like production."""
        body = (await client.get("/api/v1/health")).json()
        joined = " ".join(body["degradations"]).lower()
        assert "sqlite" in joined
        assert "in-process" in joined


class TestReadiness:
    async def test_checks_the_database(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 200
        assert response.json()["checks"]["database"] == "ok"

    async def test_reports_which_event_bus_is_active(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/api/v1/health/ready")).json()
        assert body["checks"]["event_bus"] == "in_process"


class TestMetrics:
    async def test_exposes_prometheus_text(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/metrics")
        assert response.status_code == 200
        assert "fhe_provider_requests_total" in response.text


class TestObservability:
    async def test_every_response_carries_a_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.headers["x-request-id"]

    async def test_an_inbound_request_id_is_preserved(self, client: httpx.AsyncClient) -> None:
        """A trace must survive a proxy hop."""
        response = await client.get("/api/v1/health", headers={"x-request-id": "trace-me-123"})
        assert response.headers["x-request-id"] == "trace-me-123"


class TestOpenApi:
    async def test_schema_is_published(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "/api/v1/simulations" in schema["paths"]
        assert schema["info"]["title"] == "Fantasy Health Edge API"
