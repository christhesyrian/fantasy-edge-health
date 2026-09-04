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

    # ---- access gate -------------------------------------------------------
    # One shared password in front of the whole API. Deliberately not accounts:
    # this exists so a handful of named friends can use a deployed instance,
    # and a login system nobody asked for would be more code to get wrong.
    #
    # `repr=False` keeps it out of tracebacks and logged settings dumps. Empty
    # disables the gate, which is right for local development and is refused
    # outright in production - see `access_configuration_error`.
    access_password: str = Field(default="", repr=False)
    # How long a session cookie stays valid. Long enough that a draft never ends
    # with a surprise login screen, short enough that a borrowed laptop is not
    # authorised forever.
    access_session_hours: float = 72.0
    # Failed attempts allowed per client address before that address is refused
    # for a while. A shared password is guessable by anyone patient, and this
    # is what makes patience expensive.
    access_max_attempts: int = 10
    access_lockout_minutes: float = 15.0

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

    # ---- FantasyPros (optional, licensed) ----------------------------------
    # Verified against the published OpenAPI spec at
    # https://api.fantasypros.com/public/v2/docs/fantasypros_v2_public.yml on
    # 2026-08-23: base URL, `x-api-key` header, and the two endpoints used.
    #
    # The key is personal and its terms are strict, so the limits below are the
    # provider's own, not guesses. `repr=False` keeps it out of tracebacks and
    # logged settings dumps.
    fantasypros_api_key: str = Field(default="", repr=False)
    fantasypros_base_url: str = "https://api.fantasypros.com/public/v2/json"
    fantasypros_timeout_seconds: float = 20.0
    # "Your API key will allow you to make one API call per second and up to
    # 100 API calls per day." Both are enforced; the daily count is persisted
    # so a restart cannot reset it and quietly exceed the licence.
    fantasypros_max_calls_per_day: int = 100
    fantasypros_min_seconds_between_calls: float = 1.0
    # "You should take steps to cache data on your end so that your application
    # does not poll our APIs unnecessarily." Projections move at most daily.
    fantasypros_cache_hours: float = 12.0

    @property
    def fantasypros_enabled(self) -> bool:
        """Whether a key is configured. Absent, the adapter stays disabled."""
        return bool(self.fantasypros_api_key.strip())

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
    def access_enabled(self) -> bool:
        """Whether a shared password is configured."""
        return bool(self.access_password.strip())

    @property
    def access_configuration_error(self) -> str | None:
        """Why this configuration must not start, if it must not.

        A production deployment with no password is an open database on the
        public internet, and the failure is silent: everything works, which is
        exactly the problem. Refusing to start is the only signal that cannot be
        missed, and the fix is one environment variable.
        """
        if self.is_production and not self.access_enabled:
            return (
                "PRODUCTION environment with no FHE_ACCESS_PASSWORD set. Every "
                "draft, import, and player record would be readable and writable "
                "by anyone with the URL. Set FHE_ACCESS_PASSWORD, or set FHE_ENV "
                "to development if this instance is deliberately open."
            )
        return None

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

        A managed platform's own connection string is normalised rather than
        rejected. Render, Railway, Heroku and Fly all hand out ``postgres://``,
        which SQLAlchemy 2 removed support for entirely, and none of them names
        a driver - so wiring a platform's variable straight in fails at startup
        with "Can't load plugin: sqlalchemy.dialects:postgres", which reads like
        a broken install rather than a URL scheme. An explicitly chosen driver
        is left alone; only an absent one is filled in.
        """
        if self.database_url:
            return _with_async_driver(self.database_url)
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
        if not self.access_enabled:
            warnings.append(
                "No FHE_ACCESS_PASSWORD set: the API is unauthenticated. Anyone "
                "who can reach it can read and change every draft."
            )
        if self.is_production and self.uses_sqlite:
            warnings.append(
                "PRODUCTION environment is running on the SQLite fallback. "
                "Set FHE_DATABASE_URL to a PostgreSQL instance."
            )
        return warnings


def _with_async_driver(url: str) -> str:
    """Give a database URL the async driver this application needs.

    Args:
        url: A connection string, possibly in a platform's own dialect.

    Returns:
        The same URL with an async driver named, or unchanged when one already
        is. A scheme this does not recognise is returned untouched, so an
        unusual but valid URL fails in SQLAlchemy with SQLAlchemy's own message
        rather than being silently rewritten into something else.

    Examples:
        >>> _with_async_driver("postgres://u:p@host/db")
        'postgresql+asyncpg://u:p@host/db'
        >>> _with_async_driver("postgresql+psycopg://u:p@host/db")
        'postgresql+psycopg://u:p@host/db'
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    if scheme in _POSTGRES_DRIVERLESS_SCHEMES:
        return f"postgresql+asyncpg://{rest}"
    if scheme == "sqlite":
        return f"sqlite+aiosqlite://{rest}"
    return url


# Schemes that name PostgreSQL but no driver. "postgres" is the one every
# managed platform emits and the one SQLAlchemy 2 no longer accepts.
_POSTGRES_DRIVERLESS_SCHEMES: frozenset[str] = frozenset({"postgres", "postgresql"})


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so configuration is parsed once. Tests clear the cache via
    ``get_settings.cache_clear()`` rather than mutating a global.
    """
    return Settings()
