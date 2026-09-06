from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_tts_default_voice_by_language(value: object) -> dict[str, str]:
    """Parse ``bcp47=voiceId`` comma pairs (or a mapping) into a language→voice map."""
    if value is None:
        return {}
    if isinstance(value, dict):
        parsed: dict[str, str] = {}
        for key, voice in value.items():
            language = str(key).strip()
            voice_id = str(voice).strip()
            if language and voice_id:
                parsed[language] = voice_id
        return parsed
    if isinstance(value, str):
        parsed = {}
        for fragment in value.split(","):
            item = fragment.strip()
            if not item or "=" not in item:
                continue
            language, _, voice_id = item.partition("=")
            language = language.strip()
            voice_id = voice_id.strip()
            if language and voice_id:
                parsed[language] = voice_id
        return parsed
    raise TypeError("TTS_DEFAULT_VOICE_BY_LANGUAGE must be a string or mapping")


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
    tts_default_voice_by_language: str = Field(default="")


@lru_cache
def get_settings() -> Settings:
    return Settings()
