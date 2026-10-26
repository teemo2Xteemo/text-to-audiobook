from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from app.domain.jobs import OutputFormat

_CONCAT_LINE = "file '{path}'"
_FFMPEG_NAMES = ("ffmpeg", "ffmpeg.exe")
_FFPROBE_NAMES = ("ffprobe", "ffprobe.exe")
NORMALIZE_SAMPLE_RATE_HZ = 44100
NORMALIZE_CHANNELS = 1


def backend_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "app").is_dir():
            return parent
    return here.parents[2]


def project_ffmpeg_path(root: Path | None = None) -> Path:
    base = root if root is not None else backend_root()
    unix = base / "bin" / "ffmpeg"
    windows = base / "bin" / "ffmpeg.exe"
    if windows.is_file() and not unix.is_file():
        return windows
    return unix


def resolve_ffmpeg_executable(
    *,
    which: Callable[[str], str | None] | None = None,
    project_root: Path | None = None,
) -> str | None:
    """Prefer host PATH; fall back to ``backend/bin/ffmpeg`` in the project tree."""
    lookup = shutil.which if which is None else which
    for name in _FFMPEG_NAMES:
        found = lookup(name)
        if found:
            return found
    bundled = project_ffmpeg_path(project_root)
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return None


def project_ffprobe_path(root: Path | None = None) -> Path:
    base = root if root is not None else backend_root()
    unix = base / "bin" / "ffprobe"
    windows = base / "bin" / "ffprobe.exe"
    if windows.is_file() and not unix.is_file():
        return windows
    return unix


def resolve_ffprobe_executable(
    *,
    which: Callable[[str], str | None] | None = None,
    project_root: Path | None = None,
) -> str | None:
    """Prefer host PATH; fall back to ``backend/bin/ffprobe`` in the project tree."""
    lookup = shutil.which if which is None else which
    for name in _FFPROBE_NAMES:
        found = lookup(name)
        if found:
            return found
    bundled = project_ffprobe_path(project_root)
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return None


def concat_argv(
    list_file: Path,
    destination: Path,
    *,
    executable: str | None = None,
) -> list[str]:
    """Build an argv list for the ffmpeg concat demuxer (copy; normalize encodes first)."""
    binary = executable if executable is not None else (resolve_ffmpeg_executable() or "ffmpeg")
    return [
        binary,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(destination),
    ]


def normalize_argv(
    source: Path,
    destination: Path,
    *,
    output_format: OutputFormat,
    bitrate_kbps: int,
    executable: str | None = None,
) -> list[str]:
    """Build an argv list that encodes to a consistent codec/rate/channels."""
    binary = executable if executable is not None else (resolve_ffmpeg_executable() or "ffmpeg")
    argv = [
        binary,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-ar",
        str(NORMALIZE_SAMPLE_RATE_HZ),
        "-ac",
        str(NORMALIZE_CHANNELS),
    ]
    if output_format is OutputFormat.MP3:
        argv.extend(["-c:a", "libmp3lame", "-b:a", str(bitrate_kbps) + "k"])
    else:
        argv.extend(["-c:a", "pcm_s16le"])
    argv.append(str(destination))
    return argv


def write_concat_list(list_file: Path, sources: Sequence[Path]) -> None:
    lines = [_CONCAT_LINE.format(path=_escape_concat_path(source)) for source in sources]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _escape_concat_path(source: Path) -> str:
    return str(source.resolve()).replace("'", r"'\''")


def silence_argv(
    duration_ms: int,
    destination: Path,
    *,
    output_format: OutputFormat,
    bitrate_kbps: int,
    executable: str | None = None,
) -> list[str]:
    """Build an argv list to generate silence of specified duration."""
    binary = executable if executable is not None else (resolve_ffmpeg_executable() or "ffmpeg")
    sec = max(0, duration_ms) / 1000.0
    sec_str = f"{sec:.3f}".rstrip("0").rstrip(".") if "." in f"{sec:.3f}" else str(sec)
    if sec_str == "" or sec_str == "0":
        sec_str = "0"
    argv = [
        binary,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={NORMALIZE_SAMPLE_RATE_HZ}:cl=mono",
        "-t",
        sec_str,
        "-ar",
        str(NORMALIZE_SAMPLE_RATE_HZ),
        "-ac",
        str(NORMALIZE_CHANNELS),
    ]
    if output_format is OutputFormat.MP3:
        argv.extend(["-c:a", "libmp3lame", "-b:a", str(bitrate_kbps) + "k"])
    else:
        argv.extend(["-c:a", "pcm_s16le"])
    argv.append(str(destination))
    return argv


def ffprobe_duration_argv(
    source: Path,
    *,
    executable: str | None = None,
) -> list[str]:
    """Build an argv list to inspect audio duration via ffprobe."""
    binary = executable if executable is not None else (resolve_ffprobe_executable() or "ffprobe")
    return [
        binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]


def _build_atempo_filters(factor: float) -> list[str]:
    if factor <= 0:
        raise ValueError(f"tempo factor must be positive, got {factor}")
    filters: list[str] = []
    current = factor
    while current > 2.0:
        filters.append("atempo=2.0")
        current /= 2.0
    while current < 0.5:
        filters.append("atempo=0.5")
        current /= 0.5
    current_str = f"{current:.4f}".rstrip("0").rstrip(".")
    if current_str in ("", "1"):
        current_str = "1.0"
    filters.append(f"atempo={current_str}")
    return filters


def tempo_argv(
    source: Path,
    destination: Path,
    factor: float,
    *,
    output_format: OutputFormat,
    bitrate_kbps: int,
    executable: str | None = None,
) -> list[str]:
    """Build an argv list to adjust audio playback speed/tempo."""
    binary = executable if executable is not None else (resolve_ffmpeg_executable() or "ffmpeg")
    filters = _build_atempo_filters(factor)
    argv = [
        binary,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-filter:a",
        ",".join(filters),
        "-ar",
        str(NORMALIZE_SAMPLE_RATE_HZ),
        "-ac",
        str(NORMALIZE_CHANNELS),
    ]
    if output_format is OutputFormat.MP3:
        argv.extend(["-c:a", "libmp3lame", "-b:a", str(bitrate_kbps) + "k"])
    else:
        argv.extend(["-c:a", "pcm_s16le"])
    argv.append(str(destination))
    return argv
