"""VieNeu-TTS adapter tests (fake opener, no network).

Upstream contract (https://github.com/pnnbao97/VieNeu-TTS, apps.openai_speech,
docker/docker-compose.yml profile ``api-cpu``), pinned from the README/compose file:
- OpenAI-compatible: ``POST /v1/audio/speech`` with ``model``, ``voice`` (preset
  name, e.g. "Mai Anh"), ``input``, ``response_format`` ("wav" or "pcm").
- ``GET /v1/voices``: JSON shape NOT documented upstream. This adapter accepts
  ``{"voices": [...]}``, ``{"data": [...]}`` or a bare list whose items are a
  string or an object with ``id``/``name``/``voice``.
- ``speed``: NOT documented as honoured for the API. The adapter sends it anyway and
  sets ``honours_speed = False`` so the orchestrator applies a tempo fallback.
- Health: ``GET /health`` (server root, not under /v1). Port 8000.
- Server env: ``VIENEU_MAX_STREAMS`` (CPU default 1; extra requests get 429).
"""

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
from app.providers.tts.vieneu import VieneuTTSProvider


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
    def __init__(self, fail: BaseException | None = None, voices: Any = None) -> None:
        self.fail = fail
        self.voices = ["Mai Anh", "Bao"] if voices is None else voices
        self.calls: list[Any] = []

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.calls.append(request)
        if self.fail is not None:
            raise self.fail
        if request.full_url.endswith("/voices"):
            return FakeResponse(json.dumps({"voices": self.voices}).encode())
        if request.full_url.endswith("/health"):
            return FakeResponse(b"ok")
        return FakeResponse(b"RIFFWAV")

    def speech_calls(self) -> list[Any]:
        return [c for c in self.calls if c.full_url.endswith("/audio/speech")]


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "err", Message(), BytesIO(b"{}"))


def _provider(tmp_path: Path, opener: FakeOpener) -> VieneuTTSProvider:
    return VieneuTTSProvider(
        tmp_path,
        base_url="http://vieneu:8000/v1",
        model="vieneu-v3-turbo",
        timeout_seconds=5,
        opener=opener,
    )


def _run(p: VieneuTTSProvider, text: str = "Xin chào", voice: str = "vieneu:Mai Anh") -> Any:
    return asyncio.run(p.synthesize(text, "vi-VN", voice, TTSSettings(speed=1.2)))


def test_voices_namespaced_lazy_and_cached(tmp_path: Path) -> None:
    opener = FakeOpener()
    p = _provider(tmp_path, opener)
    assert opener.calls == []
    assert [(v.id, v.language) for v in p.voices_for("vi-VN")] == [
        ("vieneu:Mai Anh", "vi-VN"),
        ("vieneu:Bao", "vi-VN"),
    ]
    assert p.voices_for("en-US") == []
    p.all_voices()
    assert len(opener.calls) == 1
    assert opener.calls[0].full_url == "http://vieneu:8000/v1/voices"


def test_voices_object_items_and_failure(tmp_path: Path) -> None:
    p = _provider(tmp_path, FakeOpener(voices=[{"id": "A"}, {"name": "B"}]))
    assert [v.id for v in p.all_voices()] == ["vieneu:A", "vieneu:B"]
    assert _provider(tmp_path, FakeOpener(fail=urllib.error.URLError("x"))).all_voices() == []


def test_failed_or_empty_fetch_not_retried_in_burst(tmp_path: Path) -> None:
    for opener in (FakeOpener(fail=urllib.error.URLError("x")), FakeOpener(voices=[])):
        p = _provider(tmp_path, opener)
        assert p.all_voices() == [] and p.voices_for("vi-VN") == []
        assert len(opener.calls) == 1


def test_probes_use_short_timeout(tmp_path: Path) -> None:
    timeouts: list[float | None] = []

    def opener(request: Any, timeout: float | None = None) -> FakeResponse:
        timeouts.append(timeout)
        return FakeResponse(b'["A"]' if request.full_url.endswith("/voices") else b"ok")

    p = VieneuTTSProvider(
        tmp_path, base_url="http://v/v1", model="m", timeout_seconds=300, opener=opener
    )
    p.all_voices()
    asyncio.run(p.check_available())
    asyncio.run(p.synthesize("hi", "vi-VN", "vieneu:A", TTSSettings()))
    assert timeouts == [5.0, 5.0, 300]


def test_static_properties(tmp_path: Path) -> None:
    p = _provider(tmp_path, FakeOpener())
    assert p.languages() == {"vi-VN"}
    assert p.max_concurrency == 1
    assert p.honours_speed is False


def test_request_shape_and_wav(tmp_path: Path) -> None:
    opener = FakeOpener()
    art = _run(_provider(tmp_path, opener), "Xin chào [System] OK")
    req = opener.speech_calls()[0]
    assert req.full_url == "http://vieneu:8000/v1/audio/speech"
    body = json.loads(req.data)
    assert body == {
        "model": "vieneu-v3-turbo",
        "input": "Xin chào (System) OK",
        "voice": "Mai Anh",
        "response_format": "wav",
        "speed": 1.2,
    }
    assert art.path.suffix == ".wav"
    assert art.path.read_bytes() == b"RIFFWAV"


@pytest.mark.parametrize(
    "exc,etype",
    [
        (_http_error(401), ErrorType.PROVIDER_CONFIGURATION_ERROR),
        (_http_error(429), ErrorType.PROVIDER_RATE_LIMIT),
        (_http_error(503), ErrorType.TTS_FAILED),
        (TimeoutError(), ErrorType.TIMEOUT),
        (urllib.error.URLError("refused"), ErrorType.TTS_FAILED),
    ],
)
def test_error_mapping(tmp_path: Path, exc: BaseException, etype: ErrorType) -> None:
    opener = FakeOpener(voices=["Mai Anh"])
    p = _provider(tmp_path, opener)
    p.all_voices()
    opener.fail = exc
    with pytest.raises(DomainError) as ei:
        _run(p)
    assert ei.value.error_type == etype


@pytest.mark.parametrize("text", ["[ ]", "【】"])
def test_bracket_only_text_sends_nothing(tmp_path: Path, text: str) -> None:
    opener = FakeOpener()
    with pytest.raises(DomainError) as ei:
        _run(_provider(tmp_path, opener), text)
    assert ei.value.error_type == ErrorType.INVALID_INPUT
    assert opener.speech_calls() == []


def test_check_available_uses_root_health(tmp_path: Path) -> None:
    opener = FakeOpener()
    asyncio.run(_provider(tmp_path, opener).check_available())
    assert opener.calls[0].full_url == "http://vieneu:8000/health"
    bad = _provider(tmp_path, FakeOpener(fail=urllib.error.URLError("down")))
    with pytest.raises(DomainError) as ei:
        asyncio.run(bad.check_available())
    assert ei.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR


def test_factory_builds_without_network(tmp_path: Path) -> None:
    from app.config.factory import build_tts_provider
    from app.config.settings import Settings

    p = build_tts_provider(Settings(tts_provider="vieneu", storage_path=tmp_path))
    assert p.max_concurrency == 1
