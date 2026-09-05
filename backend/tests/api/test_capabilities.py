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
    assert {voice["id"] for voice in body["voices"]} == {"fake-en", "fake-ja"}
    assert all("Neural" not in voice["id"] for voice in body["voices"])


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
