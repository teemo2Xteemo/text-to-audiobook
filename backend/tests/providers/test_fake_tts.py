from __future__ import annotations

import asyncio
from pathlib import Path

from app.domain.audio import TTSSettings
from app.providers.tts.fake import FAKE_AUDIO_BYTES, FakeTTSProvider


def test_fake_audio_fixture_is_mpeg_layer_iii() -> None:
    assert len(FAKE_AUDIO_BYTES) > 100
    assert FAKE_AUDIO_BYTES[:2] == b"\xff\xfb"


def test_synthesize_writes_mpeg_bytes(tmp_path: Path) -> None:
    tts = FakeTTSProvider(output_dir=tmp_path)
    artifact = asyncio.run(
        tts.synthesize("hello", "en-US", "fake-en-US-a", TTSSettings(speed=1.0)),
    )
    assert artifact.path.read_bytes() == FAKE_AUDIO_BYTES
