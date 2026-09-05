from __future__ import annotations

import ast
import asyncio
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.application.capabilities import CapabilitiesService
from app.domain.audio import TTSSettings
from app.domain.errors import DomainError, ErrorType
from app.providers.translation.nllb import NllbTranslationProvider
from app.providers.tts.edge import EdgeTTSProvider, speed_to_edge_rate

EDGE_PATH = Path(__file__).resolve().parents[2] / "app" / "providers" / "tts" / "edge.py"

_VI_A = "vi-VN-AdapterANeural"
_VI_B = "vi-VN-AdapterBNeural"
_JA = "ja-JP-AdapterANeural"
_EN = "en-US-AdapterANeural"

_RECORDS = (
    {"ShortName": _VI_A, "Locale": "vi-VN", "FriendlyName": "Adapter A"},
    {"ShortName": _VI_B, "Locale": "vi-VN", "FriendlyName": "Adapter B"},
    {"ShortName": _JA, "Locale": "ja-JP", "FriendlyName": "Adapter JA"},
    {"ShortName": _EN, "Locale": "en-US", "FriendlyName": "Adapter EN"},
)


class FakeEdgeClient:
    def __init__(self, records: Sequence[Mapping[str, Any]] | None = None) -> None:
        self.records = list(records) if records is not None else list(_RECORDS)
        self.synthesize_calls: list[tuple[str, str, str, Path]] = []
        self.list_fail: Exception | None = None
        self.synthesize_fail: Exception | None = None

    def list_voices(self) -> Sequence[Mapping[str, Any]]:
        if self.list_fail is not None:
            raise self.list_fail
        return list(self.records)

    async def synthesize(self, text: str, voice: str, rate: str, output_path: Path) -> None:
        self.synthesize_calls.append((text, voice, rate, output_path))
        if self.synthesize_fail is not None:
            raise self.synthesize_fail
        output_path.write_bytes(b"ID3FAKE")


def _provider(
    tmp_path: Path,
    *,
    defaults: Mapping[str, str] | None = None,
    client: FakeEdgeClient | None = None,
) -> tuple[EdgeTTSProvider, FakeEdgeClient]:
    fake = client or FakeEdgeClient()
    provider = EdgeTTSProvider(
        tmp_path,
        default_voice_by_language=defaults,
        client=fake,
    )
    return provider, fake


def test_voices_for_demo_and_another_language_differ(tmp_path: Path) -> None:
    provider, _client = _provider(tmp_path)
    vi_ids = {voice.id for voice in provider.voices_for("vi-VN")}
    ja_ids = {voice.id for voice in provider.voices_for("ja-JP")}
    assert vi_ids == {_VI_A, _VI_B}
    assert ja_ids == {_JA}
    assert vi_ids.isdisjoint(ja_ids)


def test_voices_for_unknown_language_is_empty(tmp_path: Path) -> None:
    provider, _client = _provider(tmp_path)
    assert list(provider.voices_for("xx-XX")) == []


def test_default_voice_is_reordered_first(tmp_path: Path) -> None:
    provider, _client = _provider(tmp_path, defaults={"vi-VN": _VI_B})
    ids = [voice.id for voice in provider.voices_for("vi-VN")]
    assert ids[0] == _VI_B
    assert ids == [_VI_B, _VI_A]


def test_unknown_default_voice_keeps_listed_order(tmp_path: Path) -> None:
    provider, _client = _provider(tmp_path, defaults={"vi-VN": "vi-VN-MissingNeural"})
    ids = [voice.id for voice in provider.voices_for("vi-VN")]
    assert ids == [_VI_A, _VI_B]


def test_synthesize_writes_artifact(tmp_path: Path) -> None:
    provider, client = _provider(tmp_path)
    artifact = asyncio.run(provider.synthesize("hello", "ja-JP", _JA, settings=TTSSettings()))
    assert artifact.path.is_file()
    assert artifact.path.read_bytes() == b"ID3FAKE"
    assert client.synthesize_calls[0][1] == _JA
    assert client.synthesize_calls[0][2] == "+0%"


def test_synthesize_maps_speed_to_edge_rate(tmp_path: Path) -> None:
    provider, client = _provider(tmp_path)
    asyncio.run(provider.synthesize("hello", "en-US", _EN, settings=TTSSettings(speed=0.5)))
    asyncio.run(provider.synthesize("hello", "en-US", _EN, settings=TTSSettings(speed=2.0)))
    assert client.synthesize_calls[0][2] == "-50%"
    assert client.synthesize_calls[1][2] == "+100%"


def test_voice_language_mismatch_is_unsupported(tmp_path: Path) -> None:
    provider, client = _provider(tmp_path)
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.synthesize("hello", "ja-JP", _VI_A, settings=TTSSettings()))
    assert exc.value.error_type is ErrorType.UNSUPPORTED_LANGUAGE
    assert client.synthesize_calls == []


def test_sdk_error_is_tts_failed(tmp_path: Path) -> None:
    client = FakeEdgeClient()
    client.synthesize_fail = RuntimeError("backend exploded")
    provider, _ = _provider(tmp_path, client=client)
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.synthesize("hello", "en-US", _EN, settings=TTSSettings()))
    assert exc.value.error_type is ErrorType.TTS_FAILED


def test_rate_limit_maps_to_provider_rate_limit(tmp_path: Path) -> None:
    class Limited(Exception):
        status = 429

    client = FakeEdgeClient()
    provider, _ = _provider(tmp_path, client=client)
    client.synthesize_fail = Limited("slow down")
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.synthesize("hello", "en-US", _EN, settings=TTSSettings()))
    assert exc.value.error_type is ErrorType.PROVIDER_RATE_LIMIT


def test_timeout_maps_to_timeout(tmp_path: Path) -> None:
    client = FakeEdgeClient()
    provider, _ = _provider(tmp_path, client=client)
    client.synthesize_fail = TimeoutError("late")
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.synthesize("hello", "en-US", _EN, settings=TTSSettings()))
    assert exc.value.error_type is ErrorType.TIMEOUT


def test_nllb_edge_capabilities_are_not_a_single_pair(tmp_path: Path) -> None:
    translation = NllbTranslationProvider(model_id="test-model")
    provider, _client = _provider(tmp_path)
    languages = CapabilitiesService(translation=translation, tts=provider).get().languages
    assert "vi-VN" in languages
    assert "ja-JP" in languages
    assert "en-US" in languages


def test_speed_to_edge_rate() -> None:
    assert speed_to_edge_rate(1.0) == "+0%"
    assert speed_to_edge_rate(0.5) == "-50%"
    assert speed_to_edge_rate(2.0) == "+100%"


def test_edge_module_has_no_top_level_edge_tts_import() -> None:
    tree = ast.parse(EDGE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "edge_tts" not in imported


@pytest.mark.integration
def test_edge_live_synthesize_opt_in(tmp_path: Path) -> None:
    if os.environ.get("EDGE_TTS_INTEGRATION") != "1":
        pytest.skip("set EDGE_TTS_INTEGRATION=1 to call live Edge TTS")
    pytest.importorskip("edge_tts")

    provider = EdgeTTSProvider(tmp_path)
    vi_voices = list(provider.voices_for("vi-VN"))
    ja_voices = list(provider.voices_for("ja-JP"))
    assert vi_voices
    assert ja_voices
    assert {voice.id for voice in vi_voices}.isdisjoint({voice.id for voice in ja_voices})
    artifact = asyncio.run(
        provider.synthesize("Hello.", "vi-VN", vi_voices[0].id, settings=TTSSettings())
    )
    assert artifact.path.is_file() and artifact.path.stat().st_size > 0
