from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.settings import Settings, get_settings, parse_tts_default_voice_by_language


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
    monkeypatch.delenv("TTS_DEFAULT_VOICE_BY_LANGUAGE", raising=False)
    monkeypatch.delenv("RETRY_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("RETRY_BACKOFF_SECONDS", raising=False)
    monkeypatch.delenv("RQ_JOB_TIMEOUT_SECONDS", raising=False)
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
    assert settings.tts_default_voice_by_language == ""
    assert settings.retry_max_attempts == 3
    assert settings.retry_backoff_seconds == 1.0
    assert settings.rq_job_timeout_seconds == 1800


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
    monkeypatch.setenv(
        "TTS_DEFAULT_VOICE_BY_LANGUAGE",
        "ja-JP=ja-JP-AdapterANeural,en-US=en-US-AdapterANeural",
    )
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0.5")
    monkeypatch.setenv("RQ_JOB_TIMEOUT_SECONDS", "3600")
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


def test_get_settings_returns_settings() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert isinstance(settings.storage_path, Path)
