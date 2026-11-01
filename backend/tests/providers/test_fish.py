from __future__ import annotations

import asyncio
import json
import urllib.error
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from app.domain.audio import TTSSettings
from app.domain.errors import DomainError, ErrorType
from app.providers.tts.fish import FishTTSProvider, parse_fish_voices

VOICES = parse_fish_voices("vi-VN=ref1:Ngọc Lan,en-US=ref1:Narrator")


class FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class FakeOpener:
    def __init__(self, fail: BaseException | None = None, data: bytes = b"MP3DATA") -> None:
        self.fail = fail
        self.data = data
        self.calls: list[Any] = []

    @property
    def last(self) -> Any:
        return self.calls[-1]

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.calls.append(request)
        if self.fail is not None:
            raise self.fail
        return FakeResponse(self.data)


def _http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("http://x", code, "err", msg, BytesIO(b"{}"))


def _provider(tmp_path: Path, opener: FakeOpener, api_key: str | None = "k") -> FishTTSProvider:
    return FishTTSProvider(
        tmp_path,
        api_key=api_key,
        base_url="https://api.fish.audio",
        model="s2.1-pro-free",
        voices=VOICES,
        timeout_seconds=5,
        max_concurrency=2,
        opener=opener,
    )


def _run(provider: FishTTSProvider, text: str = "Xin chào", voice: str = "fish:ref1") -> Any:
    return asyncio.run(provider.synthesize(text, "vi-VN", voice, TTSSettings(speed=1.2)))


def test_parse_fish_voices_multi_language() -> None:
    assert [(v.id, v.language, v.label) for v in VOICES] == [
        ("fish:ref1", "vi-VN", "Ngọc Lan"),
        ("fish:ref1", "en-US", "Narrator"),
    ]


def test_request_shape_and_artifact(tmp_path: Path) -> None:
    opener = FakeOpener()
    art = _run(_provider(tmp_path, opener), "Xin chào [System] OK")
    req = opener.last
    assert req.full_url == "https://api.fish.audio/v1/tts"
    assert req.get_header("Model") == "s2.1-pro-free"
    assert req.get_header("Authorization") == "Bearer k"
    body = json.loads(req.data)
    assert body["text"] == "Xin chào (System) OK"
    assert body["reference_id"] == "ref1"
    assert body["format"] == "mp3" and body["mp3_bitrate"] == 128 and body["normalize"] is True
    assert body["prosody"] == {"speed": 1.2}
    assert "language" not in body
    assert art.path.read_bytes() == b"MP3DATA"


@pytest.mark.parametrize(
    "code,etype",
    [
        (401, ErrorType.PROVIDER_CONFIGURATION_ERROR),
        (402, ErrorType.PROVIDER_CONFIGURATION_ERROR),
        (400, ErrorType.INVALID_INPUT),
        (404, ErrorType.PROVIDER_CONFIGURATION_ERROR),
        (413, ErrorType.INVALID_INPUT),
        (422, ErrorType.INVALID_INPUT),
        (429, ErrorType.PROVIDER_RATE_LIMIT),
        (503, ErrorType.TTS_FAILED),
    ],
)
def test_http_errors(tmp_path: Path, code: int, etype: ErrorType) -> None:
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, FakeOpener(fail=_http_error(code))))
    assert ei.value.error_type == etype
    assert ei.value.retry_after_seconds is None


def test_402_message_names_quota(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, FakeOpener(fail=_http_error(402))))
    assert "quota" in ei.value.message


def test_429_with_retry_after_header(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, FakeOpener(fail=_http_error(429, {"Retry-After": "7"}))))
    assert ei.value.retry_after_seconds == 7.0


def test_timeout_maps_to_timeout(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, FakeOpener(fail=TimeoutError())))
    assert ei.value.error_type == ErrorType.TIMEOUT


def test_connection_error_maps_to_tts_failed(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, FakeOpener(fail=urllib.error.URLError("refused"))))
    assert ei.value.error_type == ErrorType.TTS_FAILED


def test_unknown_voice_raises_unsupported_language(tmp_path: Path) -> None:
    opener = FakeOpener()
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, opener), voice="fish:nope")
    assert ei.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE
    assert opener.calls == []


@pytest.mark.parametrize("text", ["[ ]", "【】"])
def test_bracket_only_text_does_not_call_api(tmp_path: Path, text: str) -> None:
    opener = FakeOpener()
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, opener), text)
    assert ei.value.error_type == ErrorType.INVALID_INPUT
    assert opener.calls == []


@pytest.mark.parametrize("key", [None, "", "  "])
def test_check_available_requires_key(tmp_path: Path, key: str | None) -> None:
    with pytest.raises(DomainError) as ei:
        asyncio.run(_provider(tmp_path, FakeOpener(), api_key=key).check_available())
    assert ei.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR
    asyncio.run(_provider(tmp_path, FakeOpener()).check_available())


def test_factory_builds_fish_without_key(tmp_path: Path) -> None:
    from app.config.factory import build_tts_provider
    from app.config.settings import Settings

    s = Settings(tts_provider="fish", storage_path=tmp_path, fish_audio_voices="vi-VN=r1")
    p = build_tts_provider(s)
    assert [v.id for v in p.all_voices()] == ["fish:r1"]
