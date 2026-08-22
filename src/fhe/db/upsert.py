"""Dialect-aware upserts.

Ingestion must be idempotent: re-running a season, or replaying a provider
response, has to converge on the same rows rather than duplicating or failing.
The schema already declares the uniqueness constraints that make this possible;
this module turns them into ``INSERT ... ON CONFLICT DO UPDATE`` statements.

PostgreSQL and SQLite both support the construct but expose it through separate
dialect modules, so the statement builder is selected at call time from the
bind's dialect rather than assumed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import Insert as PostgresInsert
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import Insert as SqliteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from fhe.observability import get_logger

log = get_logger(__name__)

# Only the dialect-specific constructs carry ON CONFLICT; the generic
# ``sqlalchemy.sql.dml.Insert`` does not, so the union is what callers get.
UpsertInsert = PostgresInsert | SqliteInsert

# Chunk size for bulk upserts. SQLite has a hard limit on bound parameters
# (999 by default on older builds), and a wide table times a large batch blows
# straight through it. Chunking keeps one code path working on both backends.
DEFAULT_CHUNK_ROWS = 200


class UnsupportedDialectError(RuntimeError):
    """The database in use has no supported upsert construct."""


def _insert_for(dialect_name: str, table: Table) -> UpsertInsert:
    """Return the dialect-specific INSERT construct."""
    if dialect_name == "postgresql":
        return postgres_insert(table)
    if dialect_name == "sqlite":
        return sqlite_insert(table)
    raise UnsupportedDialectError(
        f"no ON CONFLICT support wired up for dialect {dialect_name!r}; "
        "add it here rather than falling back to a read-modify-write race"
    )


def table_of(model: type[DeclarativeBase]) -> Table:
    """Return a declarative model's underlying table.

    ``Model.__table__`` is typed as the broader ``FromClause`` because a mapper
    can be attached to a subquery, so it is narrowed once here instead of being
    cast at every call site.
    """
    table = model.__table__
    if not isinstance(table, Table):
        raise TypeError(f"{model.__name__} is not mapped to a plain Table")
    return table


def _chunk_size(column_count: int) -> int:
    """Rows per statement, bounded so SQLite's parameter limit is never hit."""
    if column_count <= 0:
        return DEFAULT_CHUNK_ROWS
    # 900 leaves headroom under the conservative 999-parameter limit.
    return max(1, min(DEFAULT_CHUNK_ROWS, 900 // column_count))


async def upsert_rows(
    session: AsyncSession,
    model: type[DeclarativeBase],
    rows: Sequence[dict[str, Any]],
    *,
    conflict_columns: Sequence[str],
    update_columns: Sequence[str] | None = None,
) -> int:
    """Insert rows, updating the existing row on a uniqueness conflict.

    Args:
        session: Active async session.
        model: Target declarative model.
        rows: Row dicts. All rows must share the same keys.
        conflict_columns: The unique index columns that define "already present".
        update_columns: Columns to overwrite on conflict. Defaults to every
            supplied column except the conflict columns and ``created_at``,
            which must never be rewritten by a later ingestion.

    Returns:
        The number of rows submitted.
    """
    if not rows:
        return 0

    table = table_of(model)
    dialect_name = session.bind.dialect.name if session.bind is not None else "postgresql"
    statement = _insert_for(dialect_name, table)

    supplied = list(rows[0].keys())
    if update_columns is None:
        skip = {*conflict_columns, "created_at", "id"}
        update_columns = [c for c in supplied if c not in skip]

    for index, row in enumerate(rows):
        if set(row.keys()) != set(supplied):
            raise ValueError(
                f"row {index} has keys {sorted(row.keys())}, expected {sorted(supplied)}; "
                "heterogeneous rows would silently null out missing columns"
            )

    size = _chunk_size(len(supplied))
    for start in range(0, len(rows), size):
        chunk = rows[start : start + size]
        stmt = statement.values(chunk)
        if update_columns:
            stmt = stmt.on_conflict_do_update(
                index_elements=list(conflict_columns),
                set_={c: getattr(stmt.excluded, c) for c in update_columns},
            )
        else:
            # Nothing to update means the row is fully determined by its key.
            stmt = stmt.on_conflict_do_nothing(index_elements=list(conflict_columns))
        await session.execute(stmt)

    return len(rows)
