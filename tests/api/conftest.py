"""Fixtures for API tests."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.api.app import create_app
from fhe.config import Settings
from fhe.db import Base


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings pointed at a temporary directory, with quiet logging."""
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        log_level="WARNING",
        cors_origins="http://localhost:3000,https://app.example.com",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    """An application with its lifespan running and schema created."""
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client bound to the app, with no network involved."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@dataclass(frozen=True, slots=True)
class LiveServer:
    """A real HTTP server bound to an ephemeral port."""

    base_url: str
    app: FastAPI


def _free_port() -> int:
    """Pick an unused port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture
async def live_server(settings: Settings) -> AsyncIterator[LiveServer]:
    """Run the app under uvicorn on a real socket.

    Server-sent events cannot be tested through httpx's ASGITransport: it never
    delivers ``http.disconnect``, so a long-lived streaming response never
    completes and the read hangs. A real server exercises the actual transport,
    including disconnect handling, which is the behaviour that matters.
    """
    application = create_app(settings)
    port = _free_port()
    config = uvicorn.Config(
        application, host="127.0.0.1", port=port, log_level="error", lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.02)
    else:  # pragma: no cover - only on a pathological startup failure
        raise RuntimeError("uvicorn did not start")

    async with application.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield LiveServer(base_url=f"http://127.0.0.1:{port}", app=application)
    finally:
        server.should_exit = True
        await task


@pytest.fixture
async def simulation(client: httpx.AsyncClient) -> str:
    """A created simulation, returning its id."""
    response = await client.post(
        "/api/v1/simulations",
        json={"team_count": 12, "user_draft_slot": 5, "seed": 42, "scoring_format": "ppr"},
    )
    assert response.status_code == 201
    simulation_id: str = response.json()["simulation_id"]
    return simulation_id
