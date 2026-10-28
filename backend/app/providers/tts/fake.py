import asyncio
from collections.abc import Sequence
from os import getenv
from pathlib import Path

from app.domain.audio import AudioArtifact, TTSSettings, Voice

# Fixture voices for offline Compose — fake IDs only, never Edge *Neural names.
_DEFAULT_VOICES: tuple[Voice, ...] = (
    Voice(id="fake-en-US-a", language="en-US", label="Fake English A"),
    Voice(id="fake-zh-CN-a", language="zh-CN", label="Fake Chinese A"),
    Voice(id="fake-vi-VN-a", language="vi-VN", label="Fake Vietnamese A"),
    Voice(id="fake-ja-JP-a", language="ja-JP", label="Fake Japanese A"),
    Voice(id="fake-ko-KR-a", language="ko-KR", label="Fake Korean A"),
)


class FakeTTSProvider:
    """Offline TTS stub registered as ``TTS_PROVIDER=fake``."""

    def __init__(
        self,
        voices: Sequence[Voice] | None = None,
        output_dir: Path | None = None,
    ) -> None:
        self._voices = list(voices) if voices is not None else list(_DEFAULT_VOICES)
        self._output_dir = output_dir
        self.calls: list[tuple[str, str, str, TTSSettings]] = []
        self._count = 0

    def all_voices(self) -> Sequence[Voice]:
        return list(self._voices)

    def voices_for(self, language: str) -> Sequence[Voice]:
        return [voice for voice in self._voices if voice.language == language]

    def languages(self) -> set[str]:
        return {v.language for v in self._voices}

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        delay = _fake_stage_delay_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        self.calls.append((text, language, voice, settings))
        self._count += 1
        out_dir = self._output_dir if self._output_dir is not None else Path("/tmp/fake-tts")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"synth-{self._count:03d}.bin"
        path.write_bytes(b"FAKEAUDIO")
        return AudioArtifact(path=path)


def _fake_stage_delay_seconds() -> float:
    raw = getenv("FAKE_TTS_DELAY_SECONDS", "0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0
