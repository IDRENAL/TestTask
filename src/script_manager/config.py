"""Application configuration loaded from environment variables."""

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    The control API key intentionally has no default so an internet-facing
    deployment cannot silently start with a known credential.
    """

    model_config = SettingsConfigDict(env_prefix="SCRIPT_MANAGER_", case_sensitive=False)

    database_url: str
    api_key: SecretStr = Field(min_length=16)
    scripts_dir: Path = Path("/app/scripts")
    scheduler_timezone: str = "UTC"
    script_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    max_workers: int = Field(default=3, ge=1, le=32)

    @field_validator("scheduler_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value
