from pathlib import Path

from pydantic import Field, SecretStr, field_validator
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
    chunk_translation_concurrency: int = Field(default=2, ge=1)
    chunk_tts_concurrency: int = Field(default=2, ge=1)
    nllb_model_id: str = Field(default="facebook/nllb-200-distilled-600M", min_length=1)
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")
    ollama_translation_model: str = Field(default="translategemma:4b", min_length=1)
    ollama_http_timeout_seconds: float = Field(default=120.0, gt=0)
    gemini_api_key: SecretStr | None = Field(default=None)
    gemini_translation_model: str = Field(default="gemini-3.5-flash", min_length=1)
    gemini_base_url: str = Field(default="https://generativelanguage.googleapis.com")
    gemini_http_timeout_seconds: float = Field(default=60.0, gt=0)
    groq_api_key: SecretStr | None = Field(default=None)
    groq_translation_model: str = Field(default="qwen/qwen3.8-27b", min_length=1)
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1")
    groq_http_timeout_seconds: float = Field(default=60.0, gt=0)
    openrouter_api_key: SecretStr | None = Field(default=None)
    openrouter_translation_model: str = Field(default="deepseek/deepseek-v4-flash", min_length=1)
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")
    openrouter_http_timeout_seconds: float = Field(default=60.0, gt=0)
    language_detect_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    language_confirm_threshold: float = Field(default=0.70, ge=0.0, le=1.0)
    tts_preview_wpm: float = Field(default=150.0, gt=0.0)
    tts_preview_rate_per_minute: int = Field(default=30, ge=1)
    tts_preview_timeout_seconds: float = Field(default=15.0, gt=0.0)
    estimate_chars_per_second: float = Field(default=15.0, gt=0.0)
    estimate_translation_expansion: float = Field(default=1.2, gt=0.0)
    estimate_process_time_ratio: float = Field(default=0.15, gt=0.0)
    tts_default_voice_by_language: str = Field(default="")
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)
    max_recovery_attempts: int = Field(default=3, ge=1)
    rq_job_timeout_seconds: int = Field(default=1800, ge=1)

    @field_validator("ollama_base_url")
    @classmethod
    def ollama_base_url_must_be_http(cls, value: str) -> str:
        url = value.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must be an http or https URL")
        return url

    @field_validator("gemini_base_url")
    @classmethod
    def gemini_base_url_must_be_http(cls, value: str) -> str:
        url = value.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("GEMINI_BASE_URL must be an http or https URL")
        return url

    @field_validator("groq_base_url")
    @classmethod
    def groq_base_url_must_be_http(cls, value: str) -> str:
        url = value.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("GROQ_BASE_URL must be an http or https URL")
        return url

    @field_validator("openrouter_base_url")
    @classmethod
    def openrouter_base_url_must_be_http(cls, value: str) -> str:
        url = value.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("OPENROUTER_BASE_URL must be an http or https URL")
        return url
