"""
Unit tests for BSI application configuration.

These tests verify:

- Safe local defaults
- Environment-variable loading
- API prefix validation
- CORS validation
- Upload-extension normalization
- PostgreSQL URL enforcement
- Production safety rules
- AI configuration safety rules
- Secret masking
- Upload-size conversion
"""

import os
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from bsi.config import Environment, Settings


@pytest.fixture(autouse=True)
def isolate_settings_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Isolate every settings test from local developer configuration.

    The fixture:

    - Changes the working directory to a temporary directory so the
      project's local .env file cannot be loaded.
    - Removes existing BSI_* environment variables before each test.
    - Allows each test to define only the environment variables it needs.

    Pytest automatically restores the original directory and environment
    after each test.
    """

    monkeypatch.chdir(tmp_path)

    for variable_name in tuple(os.environ):
        if variable_name.startswith("BSI_"):
            monkeypatch.delenv(variable_name, raising=False)


@pytest.mark.unit
def test_default_settings_are_safe_for_local_development() -> None:
    """Default configuration should support safe local development."""

    settings = Settings()

    assert settings.environment is Environment.LOCAL
    assert settings.debug is False
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.cors_origins == ("http://localhost:8501",)
    assert settings.allowed_upload_extensions == (".csv", ".xlsx")
    assert settings.ai_features_enabled is False
    assert settings.ai_api_key is None


@pytest.mark.unit
def test_environment_variable_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operating-system environment variables should configure BSI."""

    monkeypatch.setenv("BSI_API_PORT", "9000")

    settings = Settings()

    assert settings.api_port == 9000


@pytest.mark.unit
def test_direct_value_has_priority_over_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit constructor values should override environment variables."""

    monkeypatch.setenv("BSI_API_PORT", "9000")

    settings = Settings(api_port=8100)

    assert settings.api_port == 8100


@pytest.mark.unit
def test_api_prefix_removes_trailing_slash() -> None:
    """The API prefix should be stored in a consistent format."""

    settings = Settings(api_v1_prefix="/api/v1/")

    assert settings.api_v1_prefix == "/api/v1"


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_prefix",
    [
        "",
        "api/v1",
        "   ",
    ],
)
def test_invalid_api_prefix_is_rejected(
    invalid_prefix: str,
) -> None:
    """An empty prefix or prefix without '/' should be rejected."""

    with pytest.raises(
        ValidationError,
        match="API prefix",
    ):
        Settings(api_v1_prefix=invalid_prefix)


@pytest.mark.unit
def test_cors_origins_are_normalized_and_deduplicated() -> None:
    """Duplicate frontend origins should be stored only once."""

    settings = Settings(
        cors_origins=(
            "http://localhost:8501/",
            "http://localhost:8501",
            "https://bsi.example.com/",
        ),
    )

    assert settings.cors_origins == (
        "http://localhost:8501",
        "https://bsi.example.com",
    )


@pytest.mark.unit
def test_invalid_cors_origin_is_rejected() -> None:
    """A CORS origin must contain HTTP or HTTPS and a valid host."""

    with pytest.raises(
        ValidationError,
        match="CORS origins must use http or https",
    ):
        Settings(cors_origins=("localhost:8501",))


@pytest.mark.unit
def test_upload_extensions_are_normalized_and_deduplicated() -> None:
    """Extensions should be lowercase, prefixed and unique."""

    settings = Settings(
        allowed_upload_extensions=(
            "CSV",
            "xlsx",
            ".XLSX",
        ),
    )

    assert settings.allowed_upload_extensions == (
        ".csv",
        ".xlsx",
    )


@pytest.mark.unit
def test_non_postgresql_database_url_is_rejected() -> None:
    """BSI should reject unsupported database engines."""

    with pytest.raises(
        ValidationError,
        match="Database URL must use PostgreSQL",
    ):
        Settings(database_url="sqlite:///bsi.db")


@pytest.mark.unit
def test_debug_mode_is_rejected_in_production() -> None:
    """Production must never run with debug mode enabled."""

    with pytest.raises(
        ValidationError,
        match="Debug mode cannot be enabled in production",
    ):
        Settings(
            environment=Environment.PRODUCTION,
            debug=True,
        )


@pytest.mark.unit
def test_placeholder_database_password_is_rejected_in_production() -> None:
    """Production cannot use the example database password."""

    with pytest.raises(
        ValidationError,
        match="placeholder password",
    ):
        Settings(environment=Environment.PRODUCTION)


@pytest.mark.unit
def test_ai_provider_is_required_when_ai_is_enabled() -> None:
    """AI features must identify the configured provider."""

    with pytest.raises(
        ValidationError,
        match="AI provider is required",
    ):
        Settings(
            ai_features_enabled=True,
            ai_api_key=SecretStr("development-secret"),
        )


@pytest.mark.unit
def test_ai_key_is_required_when_ai_is_enabled() -> None:
    """AI features must not run without an API key."""

    with pytest.raises(
        ValidationError,
        match="AI API key is required",
    ):
        Settings(
            ai_features_enabled=True,
            ai_provider="azure_openai",
        )


@pytest.mark.unit
def test_secret_value_is_not_exposed_in_settings_representation() -> None:
    """Printing Settings must not reveal the AI API key."""

    secret_value = "do-not-display-this-secret"

    settings = Settings(
        ai_api_key=SecretStr(secret_value),
    )

    assert secret_value not in repr(settings)


@pytest.mark.unit
def test_upload_size_is_converted_from_megabytes_to_bytes() -> None:
    """The derived byte limit should use binary megabytes."""

    settings = Settings(max_upload_size_mb=25)

    assert settings.max_upload_size_bytes == 25 * 1024 * 1024
