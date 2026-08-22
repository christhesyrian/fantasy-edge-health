"""Persistence layer.

PostgreSQL is the production target; SQLite is supported so the demo runs
without infrastructure. Dialect differences are handled in :mod:`fhe.db.base`.
"""

from fhe.db.base import Base
from fhe.db.session import create_engine, create_session_factory, session_scope

__all__ = ["Base", "create_engine", "create_session_factory", "session_scope"]
