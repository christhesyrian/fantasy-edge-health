"""Record real API responses so the frontend can be previewed without a backend.

Why this exists
---------------
A design tool or cloud preview environment can host a Next.js app but cannot
host Python, Postgres, and a live provider poller. Without something here, the
only ways to give it a working war room would be to fake responses in
TypeScript or to reimplement the recommendation engine in the browser. Both are
forbidden: the frontend computes nothing, and generated data must never be
mistakable for real output.

So the preview is fed *recorded output from the real system*. This module
drives the actual FastAPI application over an in-process ASGI transport and
writes exactly what the HTTP API returned. The fixtures are therefore engine
output by construction — if the contract changes, re-running this is the only
way to change them, and the frontend's own zod schemas still validate every
one.

The recording is a sequence of board snapshots taken one pick apart. That is
what lets the preview advance a draft without computing anything: stepping
forward is reading the next snapshot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from fhe.observability import get_logger

log = get_logger(__name__)

# The simulation the preview replays. Fixed so the recording is reproducible:
# re-running this command on an unchanged engine produces an identical file.
PREVIEW_SEED = 20260823
PREVIEW_TEAM_COUNT = 12
PREVIEW_USER_SLOT = 5
PREVIEW_SCORING = "ppr"

# How many single-pick snapshots to record. Each row carries its full itemised
# explanation - which is 58% of its bytes and not optional, since the score
# breakdown is the product's central claim - so the recording is bounded by
# snapshot count and depth instead. Twelve picks reaches the user's turn at
# slot 5 and shows the board turning over on both sides of it.
PREVIEW_SNAPSHOT_COUNT = 12

# Board depth per snapshot. The table renders far fewer rows than the live
# default of 120, and depth multiplies by snapshot count.
PREVIEW_BOARD_DEPTH = 40

# Player details are recorded for the players most likely to be opened: the top
# of each snapshot's board. Capped so an unusual recording cannot balloon.
PREVIEW_DETAIL_PER_SNAPSHOT = 12
PREVIEW_DETAIL_LIMIT = 60


@dataclass(frozen=True, slots=True)
class PreviewFixtures:
    """One recorded preview session."""

    generated_at: str
    engine_version: str
    seed: int
    snapshots: list[dict[str, Any]]
    players: dict[str, dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        """The on-disk shape."""
        return {
            "generated_at": self.generated_at,
            "engine_version": self.engine_version,
            "seed": self.seed,
            "warning": (
                "SYNTHETIC. Recorded from the Fantasy Health Edge demo simulator. "
                "Every player, projection, and ADP here is invented. Never present "
                "this as live data."
            ),
            "snapshots": self.snapshots,
            "players": self.players,
        }


async def capture_fixtures() -> PreviewFixtures:
    """Drive the real API and record what it returns.

    Uses the in-process ASGI transport rather than a running server so the
    recording needs no ports, no database, and no network — demo mode is
    self-contained, which is the whole reason it can be recorded at all.
    """
    # Imported here rather than at module scope: building the app pulls in the
    # whole API surface, and this module is also imported by the CLI parser.
    from fhe import __version__
    from fhe.api.app import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)

    snapshots: list[dict[str, Any]] = []
    players: dict[str, dict[str, Any]] = {}

    # The ASGI transport does not run startup, and the demo pool, registry, and
    # event bus are all built there. Entering the lifespan explicitly is what
    # makes this the real application rather than an empty shell of one.
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://preview") as client,
    ):
        created = await _post(
            client,
            "/api/v1/simulations",
            {
                "team_count": PREVIEW_TEAM_COUNT,
                "user_draft_slot": PREVIEW_USER_SLOT,
                "scoring_format": PREVIEW_SCORING,
                "seed": PREVIEW_SEED,
            },
        )
        draft_id = str(created["simulation_id"])

        for index in range(PREVIEW_SNAPSHOT_COUNT):
            board = await _get(
                client, f"/api/v1/drafts/{draft_id}/board?depth={PREVIEW_BOARD_DEPTH}"
            )
            state = await _get(client, f"/api/v1/drafts/{draft_id}")
            snapshots.append({"index": index, "board": board, "state": state})

            await _record_details(client, draft_id, board, players)

            if state.get("is_complete"):
                break
            # One pick at a time: a preview that can only jump between user
            # turns cannot demonstrate the board reacting pick by pick.
            await _post(
                client,
                f"/api/v1/simulations/{draft_id}/advance",
                {"picks": 1, "stop_at_user_turn": False},
            )

    generated_at = str(snapshots[0]["board"]["computed_at"]) if snapshots else ""
    log.info(
        "preview_fixtures_captured",
        snapshots=len(snapshots),
        players=len(players),
        seed=PREVIEW_SEED,
    )
    return PreviewFixtures(
        generated_at=generated_at,
        engine_version=__version__,
        seed=PREVIEW_SEED,
        snapshots=snapshots,
        players=players,
    )


async def _record_details(
    client: httpx.AsyncClient,
    draft_id: str,
    board: dict[str, Any],
    players: dict[str, dict[str, Any]],
) -> None:
    """Record player detail for the top of one board, up to the global cap."""
    recommendations = board.get("recommendations") or []
    for row in recommendations[:PREVIEW_DETAIL_PER_SNAPSHOT]:
        uuid = str(row["player_uuid"])
        if uuid in players or len(players) >= PREVIEW_DETAIL_LIMIT:
            continue
        players[uuid] = await _get(client, f"/api/v1/drafts/{draft_id}/players/{uuid}")


async def _get(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    """GET a JSON object, failing loudly on anything unexpected."""
    response = await client.get(path)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


async def _post(client: httpx.AsyncClient, path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST a JSON object, failing loudly on anything unexpected."""
    response = await client.post(path, json=body)
    response.raise_for_status()
    if not response.content:
        return {}
    payload: Any = response.json()
    return payload if isinstance(payload, dict) else {}


def write_fixtures(fixtures: PreviewFixtures, destination: Path) -> int:
    """Write the recording, returning the byte count."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Compact rather than indented: this file is read by a machine and shipped
    # to a browser, and indentation costs a third of its size.
    text = json.dumps(fixtures.to_json(), separators=(",", ":"), sort_keys=False)
    destination.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))
