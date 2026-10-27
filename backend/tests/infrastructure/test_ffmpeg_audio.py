from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat
from app.infrastructure.ffmpeg_audio import FFmpegAudioProcessor, run_ffmpeg_argv


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


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


def test_run_ffmpeg_surfaces_stderr_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del argv, kwargs
        return _FakeProcess(1, stderr=b"Invalid codec parameters")

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(DomainError) as exc:
        asyncio.run(run_ffmpeg_argv(["ffmpeg", "-i", "in.wav", "out.mp3"]))
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
    assert "Invalid codec parameters" in exc.value.message


def test_run_ffmpeg_redacts_server_paths_and_addresses_from_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del argv, kwargs
        stderr_sample = (
            b"[mp3 @ 0x55d7a8e201c0] Error opening "
            b"/home/user/storage/jobs/123/chunk.mp3: No such file\n"
            b"Conversion failed!"
        )
        return _FakeProcess(1, stderr=stderr_sample)

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(DomainError) as exc:
        asyncio.run(run_ffmpeg_argv(["ffmpeg", "-i", "in.wav", "out.mp3"]))
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
    assert "/home/user" not in exc.value.message
    assert "0x55d7a8e201c0" not in exc.value.message
    assert "<path>" in exc.value.message
    assert "<addr>" in exc.value.message
    assert "No such file" in exc.value.message


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


def test_ffmpeg_timeout_kills_process_and_raises_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _HangingProcess:
        def __init__(self) -> None:
            self.killed = False

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return b"", b""

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> int:
            return -9

    hanging = _HangingProcess()

    async def fake_exec(*argv: str, **kwargs: object) -> _HangingProcess:
        del argv, kwargs
        return hanging

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(DomainError) as exc:
        asyncio.run(run_ffmpeg_argv(["ffmpeg", "-i", "in.wav"], timeout_seconds=0.01))
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
    assert "timed out" in exc.value.message
    assert hanging.killed


def test_normalize_missing_source_raises_domain_error(tmp_path: Path) -> None:
    processor = FFmpegAudioProcessor(executable="ffmpeg")
    missing = tmp_path / "nonexistent.mp3"
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            processor.normalize(
                missing,
                tmp_path / "out.mp3",
                output_format=OutputFormat.MP3,
                bitrate_kbps=128,
            )
        )
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
    assert "Audio file not found" in exc.value.message


def test_merge_empty_sources_raises_domain_error(tmp_path: Path) -> None:
    processor = FFmpegAudioProcessor(executable="ffmpeg")
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            processor.merge(
                [],
                tmp_path / "out.mp3",
                output_format=OutputFormat.MP3,
                bitrate_kbps=128,
            )
        )
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
    assert "cannot merge empty audio sources" in exc.value.message


def test_merge_missing_source_raises_domain_error(tmp_path: Path) -> None:
    processor = FFmpegAudioProcessor(executable="ffmpeg")
    existing = tmp_path / "existing.mp3"
    existing.write_bytes(b"data")
    missing = tmp_path / "missing.mp3"
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            processor.merge(
                [existing, missing],
                tmp_path / "out.mp3",
                output_format=OutputFormat.MP3,
                bitrate_kbps=128,
            )
        )
    assert exc.value.error_type is ErrorType.AUDIO_PROCESSING_FAILED
    assert "Audio file not found" in exc.value.message
