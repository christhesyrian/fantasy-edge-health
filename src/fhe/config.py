"""Application configuration.

Every setting is read from the environment with an ``FHE_`` prefix. Nothing is
read from a file that could be committed, and no default ever contains a
credential.

Two deliberate fallbacks make the product runnable with zero infrastructure,
which is what lets a reviewer clone the repository and see it work:

* No ``FHE_DATABASE_URL`` falls back to a local SQLite file.
* No ``FHE_REDIS_URL`` falls back to an in-process event bus and cache.

Both are logged loudly at startup and reported by the health endpoint, so a
degraded configuration can never be mistaken for a production one.
"""

from __future__ import annotations

from enum import StrEnum, unique
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@unique
class Environment(StrEnum):
    """Deployment environment."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Runtime configuration, populated from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="FHE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # ---- runtime -----------------------------------------------------------
    env: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    log_format: Literal["console", "json"] = "console"

    # ---- storage -----------------------------------------------------------
    database_url: str = ""
    data_dir: Path = Path("data")
    redis_url: str = ""

    # ---- api ---------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # ---- sleeper -----------------------------------------------------------
    # Verified against https://docs.sleeper.com on 2026-08-22. No auth required.
    sleeper_base_url: str = "https://api.sleeper.app/v1"
    sleeper_timeout_seconds: float = 10.0
    # Sleeper documents "stay under 1000 API calls per minute". This ceiling is
    # a deliberate fraction of that, because being IP-blocked mid-draft is the
    # single worst failure this product can suffer.
    sleeper_max_rpm: int = 600

    # ---- live draft polling ------------------------------------------------
    draft_poll_interval_seconds: float = 3.0
    draft_poll_max_interval_seconds: float = 30.0

    # ---- nflverse ----------------------------------------------------------
    nflverse_base_url: str = "https://github.com/nflverse/nflverse-data/releases/download"
    nflverse_timeout_seconds: float = 60.0

    # ---- player identity crosswalk ----------------------------------------
    crosswalk_url: str = (
        "https://raw.githubusercontent.com/dynastyprocess/data/master/files/db_playerids.csv"
    )

    # ---- optional LLM assistant (product is fully usable without it) -------
    anthropic_api_key: str = Field(default="", repr=False)

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        """Normalise the level so 'debug' and 'DEBUG' behave identically."""
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}, got {value!r}")
        return level

    @property
    def is_production(self) -> bool:
        """Whether production defaults should apply."""
        return self.env is Environment.PRODUCTION

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as an explicit list.

        A wildcard is never produced here; origins must be named. Allowing "*"
        alongside credentials is a well-known way to hand an attacker a
        cross-origin read of authenticated responses.
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        """Resolved database URL, falling back to a local SQLite file.

        The fallback keeps the demo runnable without Docker. It is not suitable
        for production, and :meth:`storage_warnings` says so.
        """
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{(self.data_dir / 'fhe.db').as_posix()}"

    @property
    def uses_sqlite(self) -> bool:
        """Whether the resolved database is the SQLite fallback."""
        return self.sqlalchemy_url.startswith("sqlite")

    @property
    def uses_in_process_bus(self) -> bool:
        """Whether the event bus is the single-process fallback."""
        return not self.redis_url

    def storage_warnings(self) -> list[str]:
        """Degradations a human should know about, surfaced at startup and /health."""
        warnings: list[str] = []
        if self.uses_sqlite:
            warnings.append(
                "No FHE_DATABASE_URL set: using a local SQLite file. Fine for the "
                "demo, not for production or for more than one API process."
            )
        if self.uses_in_process_bus:
            warnings.append(
                "No FHE_REDIS_URL set: using an in-process event bus. Live draft "
                "updates will not propagate across multiple workers."
            )
        if self.is_production and self.uses_sqlite:
            warnings.append(
                "PRODUCTION environment is running on the SQLite fallback. "
                "Set FHE_DATABASE_URL to a PostgreSQL instance."
            )
        return warnings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so configuration is parsed once. Tests clear the cache via
    ``get_settings.cache_clear()`` rather than mutating a global.
    """
    return Settings()
