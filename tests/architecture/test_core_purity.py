"""Architectural boundary enforcement.

``fhe.core`` is the domain layer. It must stay free of I/O so the draft engine
can be exercised from plain unit tests, reused unchanged by the live poller and
the simulator, and reasoned about without a database.

Packaging cannot enforce this - a single distribution has no import walls - so
it is enforced here instead, by walking the AST of every core module.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.architecture

SRC = Path(__file__).parents[2] / "src" / "fhe"
CORE = SRC / "core"

# Modules that perform I/O, reach the network, or bind the domain to a
# particular delivery mechanism or storage engine.
FORBIDDEN_IN_CORE = frozenset(
    {
        "sqlalchemy",
        "alembic",
        "asyncpg",
        "aiosqlite",
        "psycopg",
        "httpx",
        "requests",
        "urllib",
        "urllib3",
        "socket",
        "aiohttp",
        "fastapi",
        "starlette",
        "uvicorn",
        "sse_starlette",
        "redis",
        "boto3",
        "botocore",
        "polars",
        "pyarrow",
        "pandas",
        "sklearn",
        "numpy",
        "os",
        "pathlib",
        "shutil",
        "subprocess",
        "tempfile",
        "sqlite3",
        "fhe.db",
        "fhe.api",
        "fhe.data",
        "fhe.worker",
        "fhe.ml",
    }
)


def _core_modules() -> list[Path]:
    return sorted(CORE.rglob("*.py"))


def _imported_roots(tree: ast.AST) -> set[str]:
    """Every top-level module name imported by a parsed module."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module)
            roots.add(node.module.split(".")[0])
            # Catch `from fhe.db import x` as well as `from fhe import db`.
            parts = node.module.split(".")
            for index in range(1, len(parts) + 1):
                roots.add(".".join(parts[:index]))
    return roots


def test_core_modules_exist() -> None:
    """Guards against the test silently passing on an empty glob."""
    assert len(_core_modules()) >= 10


@pytest.mark.parametrize("module", _core_modules(), ids=lambda p: str(p.name))
def test_core_module_performs_no_io(module: Path) -> None:
    tree = ast.parse(module.read_text(), filename=str(module))
    violations = _imported_roots(tree) & FORBIDDEN_IN_CORE
    assert not violations, (
        f"{module.relative_to(SRC)} imports {sorted(violations)}. "
        "fhe.core must stay pure; move I/O to fhe.data, fhe.db, or fhe.api."
    )


@pytest.mark.parametrize("module", _core_modules(), ids=lambda p: str(p.name))
def test_core_module_has_a_docstring(module: Path) -> None:
    tree = ast.parse(module.read_text(), filename=str(module))
    assert ast.get_docstring(tree), f"{module.relative_to(SRC)} has no module docstring"


def test_no_bare_except_anywhere_in_source() -> None:
    """A bare ``except:`` swallows KeyboardInterrupt and hides real failures."""
    offenders: list[str] = []
    for module in sorted(SRC.rglob("*.py")):
        tree = ast.parse(module.read_text(), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                offenders.append(f"{module.relative_to(SRC)}:{node.lineno}")
    assert not offenders, f"bare except found at: {offenders}"
