from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.settings import Settings, parse_tts_default_voice_by_language


def test_settings_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    monkeypatch.delenv("OUTPUT_BITRATE_KBPS", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("TRANSLATION_PROVIDER", raising=False)
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.delenv("WORKER_CONCURRENCY", raising=False)
    monkeypatch.delenv("NLLB_MODEL_ID", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_TRANSLATION_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HTTP_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("LANGUAGE_DETECT_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("LANGUAGE_CONFIRM_THRESHOLD", raising=False)
    monkeypatch.delenv("TTS_PREVIEW_RATE_PER_MINUTE", raising=False)
    monkeypatch.delenv("ESTIMATE_CHARS_PER_SECOND", raising=False)
    monkeypatch.delenv("ESTIMATE_TRANSLATION_EXPANSION", raising=False)
    monkeypatch.delenv("TTS_DEFAULT_VOICE_BY_LANGUAGE", raising=False)
    monkeypatch.delenv("RETRY_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("RETRY_BACKOFF_SECONDS", raising=False)
    monkeypatch.delenv("RQ_JOB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CHUNK_TRANSLATION_CONCURRENCY", raising=False)
    monkeypatch.delenv("CHUNK_TTS_CONCURRENCY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.storage_path == Path("storage")
    assert settings.output_bitrate_kbps == 128
    assert settings.max_upload_bytes == 2_000_000
    assert settings.translation_provider == "fake"
    assert settings.tts_provider == "fake"
    assert settings.worker_concurrency == 1
    assert settings.nllb_model_id == "facebook/nllb-200-distilled-600M"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"
    assert settings.ollama_translation_model == "translategemma:4b"
    assert settings.ollama_http_timeout_seconds == 120.0
    assert settings.language_detect_min_confidence == 0.5
    assert settings.language_confirm_threshold == 0.70
    assert settings.LANGUAGE_CONFIRM_THRESHOLD == 0.70
    assert settings.tts_preview_rate_per_minute == 150
    assert settings.TTS_PREVIEW_RATE_PER_MINUTE == 150
    assert settings.estimate_chars_per_second == 15.0
    assert settings.ESTIMATE_CHARS_PER_SECOND == 15.0
    assert settings.estimate_translation_expansion == 1.2
    assert settings.ESTIMATE_TRANSLATION_EXPANSION == 1.2
    assert settings.tts_default_voice_by_language == ""
    assert settings.retry_max_attempts == 3
    assert settings.retry_backoff_seconds == 1.0
    assert settings.rq_job_timeout_seconds == 1800
    assert settings.gemini_api_key is None
    assert settings.gemini_translation_model == "gemini-3.5-flash"
    assert settings.gemini_base_url == "https://generativelanguage.googleapis.com"
    assert settings.gemini_http_timeout_seconds == 60.0
    assert settings.groq_api_key is None
    assert settings.groq_translation_model == "qwen/qwen3.8-27b"
    assert settings.groq_base_url == "https://api.groq.com/openai/v1"
    assert settings.groq_http_timeout_seconds == 60.0
    assert settings.openrouter_api_key is None
    assert settings.openrouter_translation_model == "deepseek/deepseek-v4-flash"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openrouter_http_timeout_seconds == 60.0
    assert settings.chunk_translation_concurrency == 2
    assert settings.chunk_tts_concurrency == 2


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/1")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("OUTPUT_BITRATE_KBPS", "96")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1000")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "fake")
    monkeypatch.setenv("TTS_PROVIDER", "fake")
    monkeypatch.setenv("WORKER_CONCURRENCY", "2")
    monkeypatch.setenv("NLLB_MODEL_ID", "facebook/nllb-200-distilled-1.3B")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    monkeypatch.setenv("OLLAMA_TRANSLATION_MODEL", "translategemma:12b")
    monkeypatch.setenv("OLLAMA_HTTP_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("LANGUAGE_DETECT_MIN_CONFIDENCE", "0.7")
    monkeypatch.setenv("LANGUAGE_CONFIRM_THRESHOLD", "0.85")
    monkeypatch.setenv("TTS_PREVIEW_RATE_PER_MINUTE", "180")
    monkeypatch.setenv("ESTIMATE_CHARS_PER_SECOND", "20.0")
    monkeypatch.setenv("ESTIMATE_TRANSLATION_EXPANSION", "1.5")
    monkeypatch.setenv(
        "TTS_DEFAULT_VOICE_BY_LANGUAGE",
        "ja-JP=ja-JP-AdapterANeural,en-US=en-US-AdapterANeural",
    )
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("RQ_JOB_TIMEOUT_SECONDS", "3600")
    monkeypatch.setenv("CHUNK_TRANSLATION_CONCURRENCY", "4")
    monkeypatch.setenv("CHUNK_TTS_CONCURRENCY", "3")
    settings = Settings(_env_file=None)
    assert settings.redis_url == "redis://example:6379/1"
    assert settings.storage_path == tmp_path
    assert settings.output_bitrate_kbps == 96
    assert settings.max_upload_bytes == 1000
    assert settings.translation_provider == "fake"
    assert settings.tts_provider == "fake"
    assert settings.worker_concurrency == 2
    assert settings.nllb_model_id == "facebook/nllb-200-distilled-1.3B"
    assert settings.ollama_base_url == "http://host.docker.internal:11434"
    assert settings.ollama_translation_model == "translategemma:12b"
    assert settings.ollama_http_timeout_seconds == 90.0
    assert settings.language_detect_min_confidence == 0.7
    assert settings.language_confirm_threshold == 0.85
    assert settings.tts_preview_rate_per_minute == 180
    assert settings.estimate_chars_per_second == 20.0
    assert settings.estimate_translation_expansion == 1.5
    assert settings.tts_default_voice_by_language == (
        "ja-JP=ja-JP-AdapterANeural,en-US=en-US-AdapterANeural"
    )
    assert parse_tts_default_voice_by_language(settings.tts_default_voice_by_language) == {
        "ja-JP": "ja-JP-AdapterANeural",
        "en-US": "en-US-AdapterANeural",
    }
    assert settings.retry_max_attempts == 5
    assert settings.retry_backoff_seconds == 0.5
    assert settings.rq_job_timeout_seconds == 3600
    assert settings.chunk_translation_concurrency == 4
    assert settings.chunk_tts_concurrency == 3


def test_parse_tts_default_voice_by_language_skips_malformed() -> None:
    parsed = parse_tts_default_voice_by_language(
        "ja-JP=ja-JP-AdapterANeural,not-a-pair,en-US=, =skip"
    )
    assert parsed == {"ja-JP": "ja-JP-AdapterANeural"}
    assert parse_tts_default_voice_by_language("") == {}
    assert parse_tts_default_voice_by_language(None) == {}


def test_ollama_http_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_http_timeout_seconds=0)


def test_ollama_base_url_rejects_empty_and_non_http() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_base_url="")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_base_url="file:///tmp/ollama")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ollama_base_url="ftp://127.0.0.1:11434")
    settings = Settings(_env_file=None, ollama_base_url="https://ollama.example:11434")
    assert settings.ollama_base_url == "https://ollama.example:11434"


def test_config_module_does_not_export_global_get_settings_singleton() -> None:
    import app.config as config_pkg
    import app.config.settings as settings_mod

    assert not hasattr(settings_mod, "get_settings")
    assert not hasattr(config_pkg, "get_settings")


def test_gemini_base_url_validation() -> None:
    with pytest.raises(ValidationError, match="GEMINI_BASE_URL"):
        Settings(_env_file=None, gemini_base_url="ftp://invalid.com")


def test_gemini_api_key_secret_str() -> None:
    settings = Settings(_env_file=None, gemini_api_key="super-secret-key")
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "super-secret-key"
    assert "super-secret-key" not in str(settings.gemini_api_key)


def test_groq_settings_and_validation() -> None:
    with pytest.raises(ValidationError, match="GROQ_BASE_URL"):
        Settings(_env_file=None, groq_base_url="ftp://invalid.com")
    settings = Settings(_env_file=None, groq_api_key="gsk_secret_key")
    assert settings.groq_api_key is not None
    assert settings.groq_api_key.get_secret_value() == "gsk_secret_key"
    assert "gsk_secret_key" not in str(settings.groq_api_key)


def test_openrouter_settings_and_validation() -> None:
    with pytest.raises(ValidationError, match="OPENROUTER_BASE_URL"):
        Settings(_env_file=None, openrouter_base_url="ftp://invalid.com")
    settings = Settings(_env_file=None, openrouter_api_key="sk-or-secret_key")
    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "sk-or-secret_key"
    assert "sk-or-secret_key" not in str(settings.openrouter_api_key)
