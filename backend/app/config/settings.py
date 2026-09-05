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
    output_bitrate_kbps: int = Field(default=128)
    max_upload_bytes: int = Field(default=2_000_000)
    translation_provider: str = Field(default="fake")
    tts_provider: str = Field(default="fake")
    worker_concurrency: int = Field(default=1, ge=1)
    nllb_model_id: str = Field(default="facebook/nllb-200-distilled-600M", min_length=1)
    language_detect_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
