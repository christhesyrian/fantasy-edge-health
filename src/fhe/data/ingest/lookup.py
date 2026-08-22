"""Identifier lookups against the persisted player table.

Historical datasets are keyed by ``gsis_id``, while the internal key is
``player_uuid``. Every historical ingestion needs the mapping between them, so
it is loaded once per job rather than queried per row.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fhe.db.models.player import PlayerExternalId
from fhe.observability import get_logger

log = get_logger(__name__)


async def load_external_id_map(session: AsyncSession, system: str) -> dict[str, str]:
    """Map a provider's identifiers to internal player uuids.

    Args:
        session: Active session.
        system: Identifier system, e.g. ``"gsis_id"``.

    Returns:
        ``{external_id: player_uuid}``. Loaded in one query because the
        alternative - a lookup per row - turns a 6,000-row season ingestion into
        6,000 round trips.
    """
    result = await session.execute(
        select(PlayerExternalId.external_id, PlayerExternalId.player_uuid).where(
            PlayerExternalId.system == system
        )
    )
    mapping: dict[str, str] = dict(result.tuples().all())
    log.info("external_id_map_loaded", system=system, entries=len(mapping))
    return mapping
