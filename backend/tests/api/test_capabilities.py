from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.capabilities import CapabilitiesService
from app.config.settings import Settings
from app.domain.audio import Voice
from app.main import create_app
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.tts.fake import FakeTTSProvider


@pytest.fixture
def capabilities_client(tmp_path: Path) -> Generator[TestClient, None, None]:
    translation = FakeTranslationProvider(languages=["en-US", "ja-JP", "ko-KR"])
    tts = FakeTTSProvider(
        voices=[
            Voice(id="fake-en", language="en-US", label="English"),
            Voice(id="fake-ja", language="ja-JP", label="Japanese"),
            Voice(id="extra-zh", language="zh-CN", label="Chinese only on TTS"),
        ],
        output_dir=tmp_path / "tts",
    )
    settings = Settings(_env_file=None, storage_path=tmp_path)
    app = create_app(settings)
    app.state.capabilities_service = CapabilitiesService(translation=translation, tts=tts)
    with TestClient(app) as client:
        yield client


def test_capabilities_intersection_and_all_voices(capabilities_client: TestClient) -> None:
    response = capabilities_client.get("/api/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["languages"] == ["en-US", "ja-JP"]
    assert body["narrate_languages"] == ["en-US", "ja-JP", "zh-CN"]
    assert {voice["id"] for voice in body["voices"]} == {"fake-en", "fake-ja", "extra-zh"}
    assert all("Neural" not in voice["id"] for voice in body["voices"])


def test_capabilities_unfiltered_includes_voices_for_tts_only_languages(
    capabilities_client: TestClient,
) -> None:
    response = capabilities_client.get("/api/capabilities")
    assert response.status_code == 200
    body = response.json()
    zh_voices = [v for v in body["voices"] if v["language"] == "zh-CN"]
    assert len(zh_voices) == 1
    assert zh_voices[0]["id"] == "extra-zh"


def test_capabilities_unfiltered_preserves_default_voice_ordering(tmp_path: Path) -> None:
    from app.providers.tts.edge import EdgeTTSProvider

    records = [
        {"ShortName": "vi-VN-AdapterANeural", "Locale": "vi-VN", "FriendlyName": "A"},
        {"ShortName": "vi-VN-AdapterBNeural", "Locale": "vi-VN", "FriendlyName": "B"},
    ]

    class _Client:
        def list_voices(self) -> list[dict[str, str]]:
            return records

    tts = EdgeTTSProvider(
        tmp_path,
        default_voice_by_language={"vi-VN": "vi-VN-AdapterBNeural"},
        client=_Client(),
    )
    translation = FakeTranslationProvider(languages=["vi-VN"])
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    app.state.capabilities_service = CapabilitiesService(translation=translation, tts=tts)

    with TestClient(app) as client:
        res = client.get("/api/capabilities")
        assert res.status_code == 200
        vi_voices = [v for v in res.json()["voices"] if v["language"] == "vi-VN"]
        assert len(vi_voices) == 2
        assert vi_voices[0]["id"] == "vi-VN-AdapterBNeural"


def test_capabilities_returns_translation_profiles(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path)
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.get("/api/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert "translation_profiles" in data
        assert isinstance(data["translation_profiles"], list)
        assert len(data["translation_profiles"]) >= 1
        assert data["translation_profiles"][0]["id"] == "fake"
        assert len(data["translation_profiles"][0]["supported_languages"]) >= 1


def test_capabilities_returns_multiple_profiles_when_configured(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="gemini",
        gemini_api_key="valid-key",
        tts_provider="fake",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        response = test_client.get("/api/capabilities")
        assert response.status_code == 200
        data = response.json()
        profiles = data["translation_profiles"]
        ids = [p["id"] for p in profiles]
        assert "gemini-3.5-flash" in ids
        assert "gemini-2.0-flash" in ids
        default_p = next(p for p in profiles if p["is_default"])
        assert default_p["id"] == "gemini-3.5-flash"
        assert len(default_p["supported_languages"]) > 0


def test_capabilities_filters_voices_by_language(capabilities_client: TestClient) -> None:
    response = capabilities_client.get("/api/capabilities", params={"language": "ja-JP"})
    assert response.status_code == 200
    body = response.json()
    assert body["languages"] == ["en-US", "ja-JP"]
    assert body["voices"] == [{"id": "fake-ja", "language": "ja-JP", "label": "Japanese"}]


def test_capabilities_unknown_language_returns_empty_voices(
    capabilities_client: TestClient,
) -> None:
    response = capabilities_client.get("/api/capabilities", params={"language": "xx-XX"})
    assert response.status_code == 200
    assert response.json()["voices"] == []


def test_get_capabilities_service_caches_instance_on_app_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    translation = FakeTranslationProvider(languages=["en-US"])
    tts = FakeTTSProvider(
        voices=[Voice(id="fake-en", language="en-US", label="English")],
        output_dir=tmp_path / "tts",
    )
    service = CapabilitiesService(translation=translation, tts=tts)
    calls = 0

    def _fake_build(settings: Settings) -> CapabilitiesService:
        nonlocal calls
        del settings
        calls += 1
        return service

    monkeypatch.setattr("app.api.deps.build_capabilities_service", _fake_build)
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/capabilities").status_code == 200
        assert client.get("/api/capabilities").status_code == 200
    assert calls == 1
    assert app.state.capabilities_service is service


def test_security_headers_present(capabilities_client: TestClient) -> None:
    response = capabilities_client.get("/api/capabilities")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_capabilities_includes_narrate_languages(client: TestClient) -> None:
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    data = res.json()
    assert "narrate_languages" in data
    assert "estimate_defaults" in data
    assert len(data["narrate_languages"]) >= len(data["languages"])


@pytest.mark.parametrize(("provider", "expected"), [("none", False), ("groq", True)])
def test_capabilities_reports_text_normalization(
    tmp_path: Path, provider: str, expected: bool
) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path, text_normalizer_provider=provider)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/capabilities").json()["text_normalization"] is expected


def test_text_normalizer_provider_must_be_supported(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, storage_path=tmp_path, text_normalizer_provider="gemini")


def test_build_text_normalizer_reuses_chat_provider_settings(tmp_path: Path) -> None:
    from app.config.factory import (
        ProviderConfigurationError,
        build_text_normalizer,
        cache_identity_from_settings,
    )

    off = Settings(_env_file=None, storage_path=tmp_path)
    assert build_text_normalizer(off) is None
    assert cache_identity_from_settings(off).normalizer_provider == ""

    keyless = Settings(_env_file=None, storage_path=tmp_path, text_normalizer_provider="groq")
    with pytest.raises(ProviderConfigurationError):
        build_text_normalizer(keyless)

    configured = Settings(
        _env_file=None,
        storage_path=tmp_path,
        text_normalizer_provider="openrouter",
        openrouter_api_key="sk-or-test",
    )
    assert build_text_normalizer(configured) is not None
    identity = cache_identity_from_settings(configured)
    assert identity.normalizer_provider == "openrouter"
    assert identity.normalizer_model == configured.openrouter_translation_model
    # The translation side of the cache key is untouched by the normalizer choice.
    assert identity.translation_provider == "fake"
