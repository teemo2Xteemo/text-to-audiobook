from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.infrastructure.ffmpeg import (
    ffprobe_duration_argv,
    project_ffprobe_path,
    resolve_ffmpeg_executable,
    resolve_ffprobe_executable,
    silence_argv,
    tempo_argv,
)
from app.infrastructure.ffmpeg_audio import FFmpegAudioProcessor, run_ffprobe_argv


@pytest.fixture(autouse=True)
def mock_ffmpeg_if_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ffmpeg/ffprobe is not installed on the system, mock subprocess execution."""
    if resolve_ffmpeg_executable() is not None and resolve_ffprobe_executable() is not None:
        return

    class _FakeProcess:
        def __init__(self, stdout: bytes = b"", returncode: int = 0) -> None:
            self._stdout = stdout
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, b""

    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del kwargs
        cmd = argv[0]
        # Silence generation
        if "lavfi" in argv or any("anullsrc" in arg for arg in argv):
            dest = Path(argv[-1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"mock_silence_mp3")
            return _FakeProcess(returncode=0)
        # Tempo adjustment
        if "-filter:a" in argv or any("atempo" in arg for arg in argv):
            dest = Path(argv[-1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"mock_tempo_mp3")
            return _FakeProcess(returncode=0)
        # FFprobe duration check
        if "ffprobe" in cmd or "-show_entries" in argv:
            return _FakeProcess(stdout=b"1.200000\n", returncode=0)
        return _FakeProcess(returncode=0)

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr(
        "app.infrastructure.ffmpeg_audio.resolve_ffmpeg_executable",
        lambda *args, **kwargs: "ffmpeg",
    )
    monkeypatch.setattr(
        "app.infrastructure.ffmpeg_audio.resolve_ffprobe_executable",
        lambda *args, **kwargs: "ffprobe",
    )


def test_generate_silence_and_measure_duration(tmp_path: Path) -> None:
    async def _run() -> None:
        processor = FFmpegAudioProcessor()
        silence_file = tmp_path / "silence.mp3"
        artifact = await processor.generate_silence(
            duration_ms=1200,
            destination=silence_file,
            output_format=OutputFormat.MP3,
            bitrate_kbps=128,
        )
        assert artifact.path.is_file()
        duration = await processor.get_duration_ms(silence_file)
        assert 1100 <= duration <= 1300

    asyncio.run(_run())


def test_adjust_tempo(tmp_path: Path) -> None:
    async def _run() -> None:
        processor = FFmpegAudioProcessor()
        source_file = tmp_path / "source.mp3"
        source_file.write_bytes(b"source_audio")
        destination_file = tmp_path / "sped_up.mp3"
        artifact = await processor.adjust_tempo(
            source=source_file,
            destination=destination_file,
            factor=1.25,
            output_format=OutputFormat.MP3,
            bitrate_kbps=128,
        )
        assert artifact.path.is_file()
        assert destination_file.is_file()

    asyncio.run(_run())


def test_silence_argv_construction() -> None:
    dest = Path("/tmp/silence.mp3")
    argv = silence_argv(
        duration_ms=1500,
        destination=dest,
        output_format=OutputFormat.MP3,
        bitrate_kbps=128,
        executable="ffmpeg",
    )
    assert argv[0] == "ffmpeg"
    assert "-f" in argv
    assert "lavfi" in argv
    assert any("anullsrc=r=44100:cl=mono" in arg for arg in argv)
    assert "-t" in argv
    t_idx = argv.index("-t")
    assert argv[t_idx + 1] in ("1.5", "1.500")
    assert "-ar" in argv
    assert "44100" in argv
    assert "libmp3lame" in argv
    assert "128k" in argv
    assert str(dest) in argv


def test_silence_argv_wav() -> None:
    dest = Path("/tmp/silence.wav")
    argv = silence_argv(
        duration_ms=500,
        destination=dest,
        output_format=OutputFormat.WAV,
        bitrate_kbps=128,
        executable="ffmpeg",
    )
    assert "pcm_s16le" in argv
    assert "libmp3lame" not in argv


def test_ffprobe_duration_argv_construction() -> None:
    src = Path("/tmp/audio.mp3")
    argv = ffprobe_duration_argv(src, executable="ffprobe")
    assert argv[0] == "ffprobe"
    assert "-v" in argv
    assert "error" in argv
    assert "-show_entries" in argv
    assert "format=duration" in argv
    assert "-of" in argv
    assert "default=noprint_wrappers=1:nokey=1" in argv
    assert str(src) in argv


def test_tempo_argv_construction() -> None:
    src = Path("/tmp/in.mp3")
    dest = Path("/tmp/out.mp3")
    argv = tempo_argv(
        source=src,
        destination=dest,
        factor=1.2,
        output_format=OutputFormat.MP3,
        bitrate_kbps=128,
        executable="ffmpeg",
    )
    assert argv[0] == "ffmpeg"
    assert "-filter:a" in argv
    filter_idx = argv.index("-filter:a")
    assert "atempo=1.2" in argv[filter_idx + 1]
    assert str(src) in argv
    assert str(dest) in argv


def test_tempo_argv_extreme_factors_chain() -> None:
    src = Path("/tmp/in.mp3")
    dest = Path("/tmp/out.mp3")
    # Factor > 2.0 should chain atempo filters
    argv_fast = tempo_argv(
        source=src,
        destination=dest,
        factor=3.0,
        output_format=OutputFormat.MP3,
        bitrate_kbps=128,
        executable="ffmpeg",
    )
    filter_idx = argv_fast.index("-filter:a")
    assert "atempo=2" in argv_fast[filter_idx + 1]
    assert "atempo=1.5" in argv_fast[filter_idx + 1]

    # Factor < 0.5 should chain atempo filters
    argv_slow = tempo_argv(
        source=src,
        destination=dest,
        factor=0.25,
        output_format=OutputFormat.MP3,
        bitrate_kbps=128,
        executable="ffmpeg",
    )
    filter_idx = argv_slow.index("-filter:a")
    assert argv_slow[filter_idx + 1] == "atempo=0.5,atempo=0.5"


def test_tempo_invalid_factor_raises(tmp_path: Path) -> None:
    async def _run() -> None:
        processor = FFmpegAudioProcessor(executable="ffmpeg")
        src = tmp_path / "audio.mp3"
        src.write_bytes(b"audio")
        with pytest.raises(DomainError) as exc_info:
            await processor.adjust_tempo(
                source=src,
                destination=tmp_path / "out.mp3",
                factor=0.0,
                output_format=OutputFormat.MP3,
                bitrate_kbps=128,
            )
        assert exc_info.value.error_type == ErrorType.AUDIO_PROCESSING_FAILED

    asyncio.run(_run())


def test_get_duration_missing_file_raises(tmp_path: Path) -> None:
    async def _run() -> None:
        processor = FFmpegAudioProcessor(executable="ffmpeg")
        with pytest.raises(DomainError) as exc_info:
            await processor.get_duration_ms(tmp_path / "nonexistent.mp3")
        assert exc_info.value.error_type == ErrorType.AUDIO_PROCESSING_FAILED

    asyncio.run(_run())


def test_adjust_tempo_missing_source_raises(tmp_path: Path) -> None:
    async def _run() -> None:
        processor = FFmpegAudioProcessor(executable="ffmpeg")
        with pytest.raises(DomainError) as exc_info:
            await processor.adjust_tempo(
                source=tmp_path / "missing.mp3",
                destination=tmp_path / "out.mp3",
                factor=1.0,
                output_format=OutputFormat.MP3,
                bitrate_kbps=128,
            )
        assert exc_info.value.error_type == ErrorType.AUDIO_PROCESSING_FAILED

    asyncio.run(_run())


def test_fake_audio_processor_timeline_methods(tmp_path: Path) -> None:
    async def _run() -> None:
        processor = FakeAudioProcessor()
        dest = tmp_path / "silence.mp3"
        artifact = await processor.generate_silence(
            duration_ms=750,
            destination=dest,
            output_format=OutputFormat.MP3,
            bitrate_kbps=128,
        )
        assert artifact.path == dest
        assert dest.is_file()
        assert len(processor.silence_calls) == 1
        assert processor.silence_calls[0] == (750, dest)

        duration = await processor.get_duration_ms(dest)
        assert duration == 750

        tempo_dest = tmp_path / "tempo.mp3"
        tempo_artifact = await processor.adjust_tempo(
            source=dest,
            destination=tempo_dest,
            factor=1.5,
            output_format=OutputFormat.MP3,
            bitrate_kbps=128,
        )
        assert tempo_artifact.path == tempo_dest
        assert tempo_dest.is_file()
        assert len(processor.tempo_calls) == 1
        # 750 / 1.5 = 500 ms
        tempo_duration = await processor.get_duration_ms(tempo_dest)
        assert tempo_duration == 500

    asyncio.run(_run())


def test_resolve_ffprobe_prefers_host_path(tmp_path: Path) -> None:
    host = tmp_path / "host" / "ffprobe"
    host.parent.mkdir()
    host.write_bytes(b"")
    host.chmod(0o755)
    project = tmp_path / "project"
    bundled = project / "bin" / "ffprobe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    bundled.chmod(0o755)

    def which(name: str) -> str | None:
        return str(host) if name == "ffprobe" else None

    resolved = resolve_ffprobe_executable(which=which, project_root=project)
    assert resolved == str(host)


def test_resolve_ffprobe_falls_back_to_project_bin(tmp_path: Path) -> None:
    project = tmp_path / "project"
    bundled = project / "bin" / "ffprobe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    bundled.chmod(0o755)
    resolved = resolve_ffprobe_executable(which=lambda _name: None, project_root=project)
    assert resolved == str(bundled)
    assert project_ffprobe_path(project) == bundled


def test_get_duration_non_numeric_output_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeProcess:
        def __init__(self, stdout: bytes) -> None:
            self._stdout = stdout
            self.returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return self._stdout, b""

    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del argv, kwargs
        return _FakeProcess(b"N/A\n")

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"data")

    processor = FFmpegAudioProcessor(executable="ffmpeg", ffprobe_executable="ffprobe")
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(processor.get_duration_ms(audio_file))
    assert exc_info.value.error_type == ErrorType.AUDIO_PROCESSING_FAILED
    assert "non-numeric" in exc_info.value.message


def test_run_ffprobe_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeProcess:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b"error"

    async def fake_exec(*argv: str, **kwargs: object) -> _FakeProcess:
        del argv, kwargs
        return _FakeProcess(1)

    monkeypatch.setattr("app.infrastructure.ffmpeg_audio.asyncio.create_subprocess_exec", fake_exec)
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(run_ffprobe_argv(["ffprobe", "-version"]))
    assert exc_info.value.error_type == ErrorType.AUDIO_PROCESSING_FAILED


def test_missing_ffprobe_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.infrastructure.ffmpeg_audio.resolve_ffprobe_executable",
        lambda: None,
    )
    processor = FFmpegAudioProcessor()
    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"data")
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(processor.get_duration_ms(audio_file))
    assert exc_info.value.error_type == ErrorType.AUDIO_PROCESSING_FAILED
    assert "ffprobe is not available" in exc_info.value.message


@pytest.mark.skipif(
    resolve_ffmpeg_executable() is None or resolve_ffprobe_executable() is None,
    reason="real ffmpeg/ffprobe not installed on host",
)
def test_real_ffmpeg_timeline_concat_mp3_timing_accuracy(tmp_path: Path) -> None:
    """Acceptance test for H1: verify 50 alternating speech/silence segments

    where speech is sourced from MP3 (mirroring real TTS output), decoded to
    PCM WAV intermediates, and concatenated before a single final MP3 encode.
    Asserts total duration and onset timing stay within +-50ms.
    """
    import re
    import subprocess
    from shutil import copyfile

    processor = FFmpegAudioProcessor()
    ffmpeg_bin = resolve_ffmpeg_executable() or "ffmpeg"

    # 1. Synthesize 200ms tone at 1000Hz encoded to MP3 first (like TTS)
    tone_mp3 = tmp_path / "tone.mp3"
    subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=44100",
            "-t",
            "0.200",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(tone_mp3),
        ],
        check=True,
        capture_output=True,
    )

    # 2. Decode MP3 to PCM WAV (as _merge_srt does before scheduling)
    tone_wav = tmp_path / "tone.wav"
    asyncio.run(
        processor.normalize(
            tone_mp3,
            tone_wav,
            output_format=OutputFormat.WAV,
            bitrate_kbps=128,
        )
    )
    tone_dur_ms = asyncio.run(processor.get_duration_ms(tone_wav))

    sources: list[Path] = []
    # 25 pairs of (silence 300ms, tone ~200ms) = 50 segments
    silence_dur_ms = 300
    for i in range(25):
        s_path = tmp_path / f"silence_{i}.wav"
        asyncio.run(
            processor.generate_silence(
                silence_dur_ms,
                s_path,
                output_format=OutputFormat.WAV,
                bitrate_kbps=128,
            )
        )
        sources.append(s_path)

        t_path = tmp_path / f"speech_{i}.wav"
        copyfile(tone_wav, t_path)
        sources.append(t_path)

    expected_total_ms = 25 * silence_dur_ms + 25 * tone_dur_ms
    expected_last_onset_ms = 25 * silence_dur_ms + 24 * tone_dur_ms

    timeline_wav = tmp_path / "timeline.wav"
    asyncio.run(
        processor.merge(
            sources,
            timeline_wav,
            output_format=OutputFormat.WAV,
            bitrate_kbps=128,
        )
    )

    final_mp3 = tmp_path / "output.mp3"
    asyncio.run(
        processor.normalize(
            timeline_wav,
            final_mp3,
            output_format=OutputFormat.MP3,
            bitrate_kbps=128,
        )
    )

    actual_duration_ms = asyncio.run(processor.get_duration_ms(final_mp3))
    # Duration within +-50ms
    assert abs(actual_duration_ms - expected_total_ms) <= 50

    # Verify last tone onset with silencedetect
    detect_res = subprocess.run(
        [
            ffmpeg_bin,
            "-hide_banner",
            "-nostdin",
            "-i",
            str(final_mp3),
            "-af",
            "silencedetect=noise=-30dB:d=0.20",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    ends = re.findall(r"silence_end:\s*(\d+\.?\d*)", detect_res.stderr)
    assert ends, f"No silence_end found in silencedetect output: {detect_res.stderr}"
    last_silence_end_ms = int(round(float(ends[-1]) * 1000))
    assert abs(last_silence_end_ms - expected_last_onset_ms) <= 50
