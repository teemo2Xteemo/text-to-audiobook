from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat
from app.infrastructure.ffmpeg_audio import FFmpegAudioProcessor, run_ffmpeg_argv


class _FakeProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


def test_normalize_uses_argv_and_writes_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "raw.bin"
    source.write_bytes(b"raw")
    destination = tmp_path / "out.mp3"
    seen: list[tuple[str, ...]] = []

    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del kwargs
        seen.append(argv)
        destination.write_bytes(b"ok")
        return _FakeProcess(0)

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    processor = FFmpegAudioProcessor(executable="ffmpeg")
    artifact = asyncio.run(
        processor.normalize(
            source,
            destination,
            output_format=OutputFormat.MP3,
            bitrate_kbps=128,
        )
    )
    assert artifact.path == destination
    assert seen
    assert seen[0][0] == "ffmpeg"
    assert "44100" in seen[0]
    assert "libmp3lame" in seen[0]


def test_merge_reuses_concat_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = tmp_path / "a.mp3"
    second = tmp_path / "b.mp3"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    destination = tmp_path / "output.mp3"
    seen: list[tuple[str, ...]] = []

    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del kwargs
        seen.append(argv)
        destination.write_bytes(b"merged")
        return _FakeProcess(0)

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    processor = FFmpegAudioProcessor(executable="ffmpeg")
    asyncio.run(
        processor.merge(
            [first, second],
            destination,
            output_format=OutputFormat.MP3,
            bitrate_kbps=128,
        )
    )
    assert seen
    assert "concat" in seen[0]
    assert "-c" in seen[0]
    assert "copy" in seen[0]


def test_run_ffmpeg_non_zero_is_audio_processing_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del argv, kwargs
        return _FakeProcess(1)

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(DomainError) as exc:
        asyncio.run(run_ffmpeg_argv(["ffmpeg", "-version"]))
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED


def test_missing_ffmpeg_is_audio_processing_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.infrastructure.ffmpeg_audio.resolve_ffmpeg_executable",
        lambda: None,
    )
    processor = FFmpegAudioProcessor()
    source = tmp_path / "raw.bin"
    source.write_bytes(b"raw")
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            processor.normalize(
                source,
                tmp_path / "out.mp3",
                output_format=OutputFormat.MP3,
                bitrate_kbps=128,
            )
        )
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
    assert "not available" in exc.value.message


def test_file_not_found_is_audio_processing_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_exec(*argv: str, **kwargs: object) -> None:
        del argv, kwargs
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(DomainError) as exc:
        asyncio.run(run_ffmpeg_argv(["ffmpeg", "-version"]))
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
