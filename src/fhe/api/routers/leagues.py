"""Connect a real league and draft.

Separate from the read-only Sleeper browsing endpoints because this is the step
with side effects: it writes the league and draft, builds a session, and starts
polling the provider. Nobody should trigger that by looking at a list.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from fhe.api.deps import (
    PollerManagerDep,
    RegistryDep,
    SessionFactoryDep,
    SettingsDep,
)
from fhe.api.mappers import league_out
from fhe.api.schemas import (
    ConnectDraftRequest,
    ConnectedDraftOut,
    PoolProvenanceOut,
)
from fhe.api.services.league_connect import connect_sleeper_draft
from fhe.data.providers.sleeper import SleeperProvider
from fhe.observability import get_logger

router = APIRouter(prefix="/leagues", tags=["leagues"])
log = get_logger(__name__)


@router.post(
    "/connect",
    response_model=ConnectedDraftOut,
    status_code=status.HTTP_201_CREATED,
    summary="Connect a Sleeper draft",
)
async def connect(
    body: ConnectDraftRequest,
    settings: SettingsDep,
    session_factory: SessionFactoryDep,
    registry: RegistryDep,
    pollers: PollerManagerDep,
) -> ConnectedDraftOut:
    """Connect a real Sleeper draft and start following it.

    The session is seeded with whatever picks have already happened, so
    connecting mid-draft shows the true board rather than an empty one.

    Polling only starts if the draft is still running and ``follow`` is set —
    there is nothing to observe in a finished draft, and starting a poller for
    one would burn the provider's rate limit for no reason.
    """
    async with SleeperProvider(settings) as sleeper:
        connected, binding, session = await connect_sleeper_draft(
            session_factory,
            sleeper,
            registry,
            league_id=body.league_id,
            draft_id=body.draft_id,
            user_id=body.user_id,
            recorder=pollers.recorder,
        )

        following = False
        if body.follow and connected.is_followable:
            # The poller needs a client that outlives this request, so it gets
            # its own rather than borrowing the one closing with this block.
            pollers.start(SleeperProvider(settings), binding, session)
            following = True

    provenance = connected.provenance
    return ConnectedDraftOut(
        draft_id=connected.session_id,
        league_id=connected.provider_league_id,
        league_name=connected.league_name,
        status=connected.draft_status.value,
        user_draft_slot=connected.user_draft_slot,
        picks_already_made=connected.picks_already_made,
        following=following,
        league=league_out(connected.league),
        pool=PoolProvenanceOut(
            player_count=provenance.player_count,
            with_projection=provenance.with_projection,
            with_adp=provenance.with_adp,
            with_health=provenance.with_health,
            projection_sources=list(provenance.projection_sources),
            adp_sources=list(provenance.adp_sources),
            warnings=list(provenance.warnings),
        ),
    )
