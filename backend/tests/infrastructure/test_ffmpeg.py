import subprocess
from pathlib import Path

import pytest

from app.domain.jobs import OutputFormat
from app.infrastructure.ffmpeg import (
    concat_argv,
    normalize_argv,
    project_ffmpeg_path,
    resolve_ffmpeg_executable,
    write_concat_list,
)


def test_concat_argv_is_plain_list() -> None:
    list_file = Path("/tmp/jobs/example/concat.txt")
    destination = Path("/tmp/jobs/example/output.mp3")
    argv = concat_argv(list_file, destination, executable="ffmpeg")
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert argv[0] == "ffmpeg"
    assert "-f" in argv
    assert "concat" in argv
    assert "-safe" in argv
    assert "0" in argv
    assert str(list_file) in argv
    assert str(destination) in argv
    assert all(";" not in part for part in argv)


def test_write_concat_list_uses_file_lines(tmp_path: Path) -> None:
    first = tmp_path / "a.mp3"
    second = tmp_path / "b.mp3"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    list_file = tmp_path / "concat.txt"
    write_concat_list(list_file, [first, second])
    body = list_file.read_text(encoding="utf-8")
    assert body.startswith("file '")
    assert str(first.resolve()) in body
    assert str(second.resolve()) in body


def test_resolve_ffmpeg_prefers_host_path(tmp_path: Path) -> None:
    host = tmp_path / "host" / "ffmpeg"
    host.parent.mkdir()
    host.write_bytes(b"")
    host.chmod(0o755)
    project = tmp_path / "project"
    bundled = project / "bin" / "ffmpeg"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    bundled.chmod(0o755)

    def which(name: str) -> str | None:
        return str(host) if name == "ffmpeg" else None

    resolved = resolve_ffmpeg_executable(which=which, project_root=project)
    assert resolved == str(host)


def test_resolve_ffmpeg_falls_back_to_project_bin(tmp_path: Path) -> None:
    project = tmp_path / "project"
    bundled = project / "bin" / "ffmpeg"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"")
    bundled.chmod(0o755)
    resolved = resolve_ffmpeg_executable(which=lambda _name: None, project_root=project)
    assert resolved == str(bundled)
    assert project_ffmpeg_path(project) == bundled


def test_resolve_ffmpeg_returns_none_when_missing(tmp_path: Path) -> None:
    assert resolve_ffmpeg_executable(which=lambda _name: None, project_root=tmp_path) is None


@pytest.mark.integration
@pytest.mark.skipif(
    resolve_ffmpeg_executable() is None,
    reason="ffmpeg not on host PATH or backend/bin",
)
def test_concat_argv_merges_silent_files(tmp_path: Path) -> None:
    executable = resolve_ffmpeg_executable()
    assert executable is not None
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    for path in (first, second):
        subprocess.run(
            [
                executable,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=44100:cl=mono",
                "-t",
                "0.05",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    list_file = tmp_path / "concat.txt"
    destination = tmp_path / "out.wav"
    write_concat_list(list_file, [first, second])
    argv = concat_argv(list_file, destination, executable=executable)
    subprocess.run(argv, check=True, capture_output=True)
    assert destination.is_file() and destination.stat().st_size > 0


def test_normalize_argv_is_plain_list_for_mp3() -> None:
    source = Path("/tmp/jobs/example/raw.mp3")
    destination = Path("/tmp/jobs/example/normalized.mp3")
    argv = normalize_argv(
        source,
        destination,
        output_format=OutputFormat.MP3,
        bitrate_kbps=128,
        executable="ffmpeg",
    )
    assert isinstance(argv, list)
    assert all(isinstance(part, str) for part in argv)
    assert argv[0] == "ffmpeg"
    assert "-ar" in argv
    assert "44100" in argv
    assert "-ac" in argv
    assert "1" in argv
    assert "libmp3lame" in argv
    assert "128k" in argv
    assert str(source) in argv
    assert str(destination) in argv
    assert all(";" not in part for part in argv)


def test_normalize_argv_wav_skips_bitrate() -> None:
    argv = normalize_argv(
        Path("/tmp/in.wav"),
        Path("/tmp/out.wav"),
        output_format=OutputFormat.WAV,
        bitrate_kbps=128,
        executable="ffmpeg",
    )
    assert "pcm_s16le" in argv
    assert "libmp3lame" not in argv
    assert "128k" not in argv


@pytest.mark.integration
@pytest.mark.skipif(
    resolve_ffmpeg_executable() is None,
    reason="ffmpeg not on host PATH or backend/bin",
)
def test_normalize_argv_encodes_silent_wav(tmp_path: Path) -> None:
    executable = resolve_ffmpeg_executable()
    assert executable is not None
    source = tmp_path / "raw.wav"
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=24000:cl=stereo",
            "-t",
            "0.05",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    destination = tmp_path / "normalized.mp3"
    argv = normalize_argv(
        source,
        destination,
        output_format=OutputFormat.MP3,
        bitrate_kbps=128,
        executable=executable,
    )
    subprocess.run(argv, check=True, capture_output=True)
    assert destination.is_file() and destination.stat().st_size > 0
