from pathlib import Path

import pytest

from app.config.settings import Settings, get_settings


def test_settings_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("STORAGE_PATH", raising=False)
    monkeypatch.delenv("OUTPUT_BITRATE_KBPS", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
    settings = Settings(_env_file=None)
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.storage_path == Path("storage")
    assert settings.output_bitrate_kbps == 128
    assert settings.max_upload_bytes == 2_000_000


def test_settings_read_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/1")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("OUTPUT_BITRATE_KBPS", "96")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1000")
    settings = Settings(_env_file=None)
    assert settings.redis_url == "redis://example:6379/1"
    assert settings.storage_path == tmp_path
    assert settings.output_bitrate_kbps == 96
    assert settings.max_upload_bytes == 1000


def test_get_settings_returns_settings() -> None:
    get_settings.cache_clear()
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert isinstance(settings.storage_path, Path)
