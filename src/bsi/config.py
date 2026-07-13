"""
Application configuration for BSI — Bank Statement Intelligence.

This module is responsible for:

- Loading configuration from environment variables and .env files.
- Converting configuration strings into typed Python values.
- Validating security-sensitive combinations.
- Providing one cached Settings instance to the application.

No real secrets should be stored in this file.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported BSI runtime environments."""

    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Supported application logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class StorageBackend(StrEnum):
    """Supported file-storage implementations."""

    LOCAL = "local"


class Settings(BaseSettings):
    """
    Validated BSI application configuration.

    Values are loaded using the following priority:

    1. Values passed directly to Settings(...)
    2. Operating-system environment variables
    3. Values stored in the local .env file
    4. Default values declared in this class

    Environment variables use the BSI_ prefix.

    Example:
        Python field:
            api_port

        Environment variable:
            BSI_API_PORT
    """

    model_config = SettingsConfigDict(
        env_prefix="BSI_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_ignore_empty=True,
        extra="ignore",
        validate_default=True,
    )

    # --------------------------------------------------------
    # Application
    # --------------------------------------------------------

    app_name: str = "BSI — Bank Statement Intelligence"

    environment: Environment = Environment.LOCAL

    debug: bool = False

    log_level: LogLevel = LogLevel.INFO

    # --------------------------------------------------------
    # FastAPI
    # --------------------------------------------------------

    api_host: str = Field(
        default="127.0.0.1",
        min_length=1,
    )

    api_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
    )

    api_v1_prefix: str = "/api/v1"

    cors_origins: tuple[str, ...] = ("http://localhost:8501",)

    # --------------------------------------------------------
    # PostgreSQL
    # --------------------------------------------------------

    database_url: str = Field(
        default=("postgresql+psycopg://bsi_user:change_me@localhost:5432/bsi"),
        min_length=1,
        repr=False,
    )

    database_echo: bool = False

    # --------------------------------------------------------
    # File storage
    # --------------------------------------------------------

    storage_backend: StorageBackend = StorageBackend.LOCAL

    local_storage_root: Path = Path("var/storage")

    max_upload_size_mb: int = Field(
        default=25,
        ge=1,
        le=1024,
    )

    allowed_upload_extensions: tuple[str, ...] = (
        ".csv",
        ".xlsx",
    )

    # --------------------------------------------------------
    # AI features
    # --------------------------------------------------------

    ai_features_enabled: bool = False

    ai_provider: str | None = None

    ai_api_key: SecretStr | None = Field(
        default=None,
        repr=False,
    )

    # --------------------------------------------------------
    # Field validation
    # --------------------------------------------------------

    @field_validator("api_v1_prefix")
    @classmethod
    def validate_api_v1_prefix(cls, value: str) -> str:
        """
        Normalize and validate the versioned API prefix.

        Parameters
        ----------
        value:
            API prefix received from defaults, .env, or the operating
            system environment.

        Returns
        -------
        str
            Normalized API prefix without a trailing slash.

        Raises
        ------
        ValueError
            If the prefix is empty or does not begin with "/".
        """

        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError("API prefix cannot be empty.")

        if not normalized_value.startswith("/"):
            raise ValueError("API prefix must begin with '/'.")

        if normalized_value != "/":
            normalized_value = normalized_value.rstrip("/")

        return normalized_value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        """
        Validate frontend origins permitted to call the BSI API.

        Each origin must use HTTP or HTTPS and contain a host.
        Duplicate values are removed while preserving their order.
        """

        if not values:
            raise ValueError("At least one CORS origin is required.")

        normalized_origins: list[str] = []

        for value in values:
            normalized_value = value.strip().rstrip("/")
            parsed_origin = urlparse(normalized_value)

            if parsed_origin.scheme not in {"http", "https"}:
                raise ValueError("CORS origins must use http or https.")

            if not parsed_origin.netloc:
                raise ValueError(f"CORS origin does not contain a valid host: {value}")

            if normalized_value not in normalized_origins:
                normalized_origins.append(normalized_value)

        return tuple(normalized_origins)

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """
        Confirm that BSI is configured to use PostgreSQL.

        BSI uses Psycopg as its PostgreSQL driver.
        """

        normalized_value = value.strip()

        supported_prefixes = (
            "postgresql+psycopg://",
            "postgresql://",
        )

        if not normalized_value.startswith(supported_prefixes):
            raise ValueError("Database URL must use PostgreSQL and Psycopg.")

        return normalized_value

    @field_validator("local_storage_root")
    @classmethod
    def validate_local_storage_root(cls, value: Path) -> Path:
        """
        Normalize the local file-storage directory.

        The path is not created here. Directory creation belongs to the
        infrastructure storage component.
        """

        normalized_path = value.expanduser()

        if normalized_path == Path("."):
            raise ValueError("Local storage root cannot be the project root.")

        return normalized_path

    @field_validator("allowed_upload_extensions")
    @classmethod
    def validate_allowed_upload_extensions(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        """
        Normalize supported upload extensions.

        Examples:
            CSV   -> .csv
            xlsx  -> .xlsx
            .XLSX -> .xlsx
        """

        if not values:
            raise ValueError("At least one upload extension must be configured.")

        normalized_extensions: list[str] = []

        for value in values:
            normalized_value = value.strip().lower()

            if not normalized_value:
                raise ValueError("Upload extensions cannot contain empty values.")

            if not normalized_value.startswith("."):
                normalized_value = f".{normalized_value}"

            if len(normalized_value) == 1:
                raise ValueError(
                    "An upload extension must contain characters after the period."
                )

            if normalized_value not in normalized_extensions:
                normalized_extensions.append(normalized_value)

        return tuple(normalized_extensions)

    # --------------------------------------------------------
    # Cross-field validation
    # --------------------------------------------------------

    @model_validator(mode="after")
    def validate_environment_configuration(self) -> Self:
        """
        Validate settings that depend on multiple fields.

        Production safety rules:

        - Debug mode must be disabled.
        - Placeholder database credentials must be replaced.

        AI safety rules:

        - Enabling AI requires a provider.
        - Enabling AI requires an API key.
        """

        if self.environment is Environment.PRODUCTION:
            if self.debug:
                raise ValueError("Debug mode cannot be enabled in production.")

            if "change_me" in self.database_url:
                raise ValueError(
                    "Production database credentials must not use "
                    "the placeholder password."
                )

        if self.ai_features_enabled:
            if not self.ai_provider or not self.ai_provider.strip():
                raise ValueError(
                    "An AI provider is required when AI features are enabled."
                )

            if (
                self.ai_api_key is None
                or not self.ai_api_key.get_secret_value().strip()
            ):
                raise ValueError(
                    "An AI API key is required when AI features are enabled."
                )

        return self

    # --------------------------------------------------------
    # Derived settings
    # --------------------------------------------------------

    @property
    def is_production(self) -> bool:
        """Return whether BSI is running in production."""

        return self.environment is Environment.PRODUCTION

    @property
    def max_upload_size_bytes(self) -> int:
        """Convert the configured upload limit from MB to bytes."""

        bytes_per_megabyte = 1024 * 1024
        return self.max_upload_size_mb * bytes_per_megabyte


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the shared validated BSI settings instance.

    The settings object is cached so that application modules do not
    repeatedly read and validate the same environment variables.

    Returns
    -------
    Settings
        Validated application configuration.
    """

    return Settings()
