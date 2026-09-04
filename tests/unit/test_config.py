"""Settings resolution, especially the parts a deployment depends on."""

from __future__ import annotations

from pathlib import Path

import pytest

from fhe.config import Environment, Settings

pytestmark = pytest.mark.unit


def settings(**overrides: object) -> Settings:
    """Settings built without reading a .env file."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class TestDatabaseUrl:
    @pytest.mark.parametrize(
        "platform_url",
        [
            "postgres://user:pw@host:5432/db",
            "postgresql://user:pw@host:5432/db",
        ],
    )
    def test_a_platforms_own_connection_string_is_made_usable(self, platform_url: str) -> None:
        """Render, Railway, Heroku and Fly all hand out `postgres://`.

        SQLAlchemy 2 removed that scheme, so wiring a platform's variable
        straight in fails at startup with "Can't load plugin:
        sqlalchemy.dialects:postgres" — which reads like a broken install
        rather than a URL scheme, and is a genuinely bad evening to debug.
        """
        assert settings(database_url=platform_url).sqlalchemy_url.startswith(
            "postgresql+asyncpg://"
        )

    def test_the_credentials_and_host_survive_the_rewrite(self) -> None:
        resolved = settings(database_url="postgres://user:pw@host:5432/db").sqlalchemy_url
        assert resolved == "postgresql+asyncpg://user:pw@host:5432/db"

    def test_an_explicitly_chosen_driver_is_left_alone(self) -> None:
        """Naming a driver is a decision, not an omission to correct."""
        url = "postgresql+psycopg://user:pw@host/db"
        assert settings(database_url=url).sqlalchemy_url == url

    def test_an_unrecognised_scheme_is_not_rewritten(self) -> None:
        """It should fail in SQLAlchemy, with SQLAlchemy's message."""
        url = "mysql://user@host/db"
        assert settings(database_url=url).sqlalchemy_url == url

    def test_no_url_falls_back_to_sqlite(self, tmp_path: Path) -> None:
        resolved = settings(data_dir=tmp_path).sqlalchemy_url
        assert resolved.startswith("sqlite+aiosqlite://")
        assert settings(data_dir=tmp_path).uses_sqlite

    def test_a_rewritten_postgres_url_is_not_mistaken_for_the_fallback(self) -> None:
        """Or a real deployment would report itself as running on SQLite."""
        assert not settings(database_url="postgres://u:p@h/db").uses_sqlite


class TestAccessGate:
    def test_a_password_enables_the_gate(self) -> None:
        assert settings(access_password="hunter2").access_enabled
        assert not settings().access_enabled

    def test_whitespace_is_not_a_password(self) -> None:
        assert not settings(access_password="   ").access_enabled

    def test_production_without_a_password_is_a_configuration_error(self) -> None:
        error = settings(
            env=Environment.PRODUCTION,
            database_url="postgres://u:p@h/db",
        ).access_configuration_error
        assert error is not None
        assert "FHE_ACCESS_PASSWORD" in error

    def test_production_with_a_password_is_fine(self) -> None:
        assert (
            settings(
                env=Environment.PRODUCTION,
                database_url="postgres://u:p@h/db",
                access_password="hunter2",
            ).access_configuration_error
            is None
        )

    def test_development_without_a_password_is_allowed(self) -> None:
        """Local work must not require inventing a password first."""
        assert settings().access_configuration_error is None

    def test_but_it_is_reported_as_a_degradation(self) -> None:
        assert any("FHE_ACCESS_PASSWORD" in w for w in settings().storage_warnings())

    def test_a_gated_instance_does_not_warn_about_the_gate(self) -> None:
        warnings = settings(access_password="hunter2").storage_warnings()
        assert not any("FHE_ACCESS_PASSWORD" in w for w in warnings)


class TestSecretsStayOutOfLogs:
    @pytest.mark.parametrize(
        ("field", "secret"),
        [
            ("access_password", "the-shared-password"),
            ("fantasypros_api_key", "fp-secret-key"),
            ("anthropic_api_key", "sk-ant-secret"),
        ],
    )
    def test_a_secret_is_not_in_the_settings_repr(self, field: str, secret: str) -> None:
        """A settings dump reaches logs and tracebacks; secrets must not."""
        assert secret not in repr(settings(**{field: secret}))
