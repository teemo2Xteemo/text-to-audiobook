from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from the environment. No secrets belong in source."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    redis_url: str = Field(default="redis://localhost:6379/0")
    storage_path: Path = Field(default=Path("storage"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
