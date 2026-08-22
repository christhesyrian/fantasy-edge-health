"""CSV import and pipeline diagnostics endpoints."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from fhe.db.models.player import Player

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded(app: FastAPI) -> FastAPI:
    """An app with two players present, so imports have something to match."""
    async with app.state.session_factory() as session:
        for uuid, name, normalized, position, team in [
            ("u-chase", "Ja'Marr Chase", "jamarrchase", "WR", "CIN"),
            ("u-bijan", "Bijan Robinson", "bijanrobinson", "RB", "ATL"),
        ]:
            session.add(
                Player(
                    player_uuid=uuid,
                    full_name=name,
                    normalized_name=normalized,
                    position=position,
                    team=team,
                    is_active=True,
                    identity_method="DIRECT_GSIS",
                    identity_confidence=1.0,
                    source="test",
                )
            )
        await session.commit()
    return app


def upload(csv: str) -> dict[str, Any]:
    """Build a multipart file payload."""
    return {"file": ("import.csv", csv.encode("utf-8"), "text/csv")}


class TestAdpImport:
    async def test_imports_matching_rows_and_reports_the_rest(
        self, client: httpx.AsyncClient, seeded: FastAPI
    ) -> None:
        response = await client.post(
            "/api/v1/imports/adp",
            files=upload(
                "player_name,position,team,adp,adp_stdev\n"
                "Ja'Marr Chase,WR,CIN,1.8,0.9\n"
                "Nobody Here,RB,SEA,40\n"
            ),
            data={"source": "my_export", "season": "2026", "scoring_format": "ppr"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rows_written"] == 1
        assert body["rows_rejected"] == 1
        assert body["rejection_reasons"] == {"no_matching_player": 1}
        # The uploader needs to see which row failed, not discover it later.
        assert body["rejection_samples"][0]["player_name"] == "Nobody Here"

    async def test_missing_column_is_refused_with_guidance(
        self, client: httpx.AsyncClient, seeded: FastAPI
    ) -> None:
        response = await client.post(
            "/api/v1/imports/adp",
            files=upload("player_name,position\nA,WR\n"),
            data={"source": "x", "season": "2026"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_csv"
        assert "data/schemas" in response.json()["detail"]

    async def test_a_byte_order_mark_does_not_break_the_header(
        self, client: httpx.AsyncClient, seeded: FastAPI
    ) -> None:
        """Spreadsheets add a BOM; it must not corrupt the first column name."""
        # encode("utf-8-sig") is what prepends the BOM; the source string must
        # not also contain one, or the file would carry two.
        csv = "player_name,position,team,adp\nJa'Marr Chase,WR,CIN,1.8\n"
        response = await client.post(
            "/api/v1/imports/adp",
            files={"file": ("import.csv", csv.encode("utf-8-sig"), "text/csv")},
            data={"source": "excel", "season": "2026"},
        )
        assert response.status_code == 200
        assert response.json()["rows_written"] == 1

    async def test_non_utf8_upload_is_refused_clearly(
        self, client: httpx.AsyncClient, seeded: FastAPI
    ) -> None:
        response = await client.post(
            "/api/v1/imports/adp",
            files={"file": ("import.csv", b"\xff\xfe\x00bad", "text/csv")},
            data={"source": "x", "season": "2026"},
        )
        assert response.status_code == 422
        assert "UTF-8" in response.json()["detail"]


class TestProjectionImport:
    async def test_imports_projections(self, client: httpx.AsyncClient, seeded: FastAPI) -> None:
        response = await client.post(
            "/api/v1/imports/projections",
            files=upload(
                "player_name,position,team,projected_points\n"
                "Ja'Marr Chase,WR,CIN,312.4\n"
                "Bijan Robinson,RB,ATL,298.1\n"
            ),
            data={"source": "my_export", "season": "2026", "scoring_format": "ppr"},
        )
        assert response.status_code == 200
        assert response.json()["rows_written"] == 2
        assert response.json()["status"] == "success"

    async def test_implausible_values_are_rejected(
        self, client: httpx.AsyncClient, seeded: FastAPI
    ) -> None:
        response = await client.post(
            "/api/v1/imports/projections",
            files=upload(
                "player_name,position,team,projected_points\nJa'Marr Chase,WR,CIN,40000\n"
            ),
            data={"source": "x", "season": "2026"},
        )
        assert response.json()["rows_written"] == 0
        assert response.json()["rows_rejected"] == 1


class TestDiagnostics:
    async def test_reports_recent_runs_including_failures(
        self, client: httpx.AsyncClient, seeded: FastAPI
    ) -> None:
        await client.post(
            "/api/v1/imports/adp",
            files=upload("player_name,position,team,adp\nJa'Marr Chase,WR,CIN,1.8\n"),
            data={"source": "good_source", "season": "2026"},
        )
        await client.post(
            "/api/v1/imports/adp",
            files=upload("player_name,position\nA,WR\n"),
            data={"source": "broken_source", "season": "2026"},
        )

        body = (await client.get("/api/v1/diagnostics/pipeline")).json()
        statuses = {
            run["source" if "source" in run else "provider"]: run["status"]
            for run in body["recent_runs"]
        }
        assert statuses.get("good_source") in {"success", "partial"}
        assert statuses.get("broken_source") == "failed"

    async def test_counts_tracked_players(self, client: httpx.AsyncClient, seeded: FastAPI) -> None:
        body = (await client.get("/api/v1/diagnostics/pipeline")).json()
        assert body["players_tracked"] == 2
        assert body["unresolved_identity_conflicts"] == 0

    async def test_empty_pipeline_is_not_an_error(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/diagnostics/pipeline")
        assert response.status_code == 200
        assert response.json()["recent_runs"] == []
