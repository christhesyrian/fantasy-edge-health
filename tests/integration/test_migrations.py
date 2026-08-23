"""Migration sanity checks.

The valuable test here is :func:`test_no_pending_model_changes`: it applies every
migration to an empty database and then asks Alembic whether the result still
differs from the models. That is what catches the most common migration
mistake - changing a model and forgetting to generate the revision - which
otherwise only surfaces on a deploy.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import fhe.db.models  # noqa: F401  -- registers metadata
from fhe.config import get_settings
from fhe.db.base import Base

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).parents[2]

# Differences Alembic reports that are not real drift. Kept deliberately tiny;
# anything added here needs a reason, because the point of the test is to fail.
IGNORED_DIFF_TYPES: frozenset[str] = frozenset()


# Environment this test must control rather than inherit. `FHE_DATABASE_URL` is
# the load-bearing one: a developer with a real Postgres URL in their .env would
# otherwise have these tests run against it, build an async engine in a sync
# context, and fail with an unrelated greenlet error. The test owns its
# database, so it states that explicitly.
_PINNED_ENV: tuple[str, ...] = ("FHE_DATA_DIR", "FHE_DATABASE_URL")


def _upgraded_engine(tmp_path: Path) -> Any:
    """Apply every migration to a fresh database and return a sync engine."""
    previous = {name: os.environ.get(name) for name in _PINNED_ENV}
    os.environ["FHE_DATA_DIR"] = str(tmp_path)
    # Empty, not absent: this forces the SQLite fallback into tmp_path whatever
    # the developer's .env says.
    os.environ["FHE_DATABASE_URL"] = ""
    get_settings.cache_clear()
    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(config, "head")
        url = get_settings().sqlalchemy_url.replace("+aiosqlite", "")
        return create_engine(url)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()
        get_settings.cache_clear()


def test_migrations_apply_cleanly_to_an_empty_database(tmp_path: Path) -> None:
    engine = _upgraded_engine(tmp_path)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert "alembic_version" in tables
    assert set(Base.metadata.tables) <= tables


def test_no_pending_model_changes(tmp_path: Path) -> None:
    """The migration head must fully describe the models.

    A non-empty diff means someone edited a model without generating a revision,
    so a deployed database would silently disagree with the code.
    """
    engine = _upgraded_engine(tmp_path)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "target_metadata": Base.metadata},
            )
            diff = [
                entry
                for entry in compare_metadata(context, Base.metadata)
                if str(entry[0]) not in IGNORED_DIFF_TYPES
            ]
    finally:
        engine.dispose()

    assert not diff, (
        "models and migrations have diverged; run:\n"
        "  ./.venv/bin/alembic revision --autogenerate -m 'describe the change'\n\n"
        f"pending differences: {diff}"
    )


def test_alembic_ini_contains_no_connection_string() -> None:
    """The URL comes from settings, so no credential is ever committed."""
    text = (REPO_ROOT / "alembic.ini").read_text()
    for line in text.splitlines():
        if line.strip().startswith("sqlalchemy.url"):
            _, _, value = line.partition("=")
            assert value.strip() == "", "alembic.ini must not carry a connection string"
