from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from app.domain.jobs import OutputFormat

_CONCAT_LINE = "file '{path}'"
_FFMPEG_NAMES = ("ffmpeg", "ffmpeg.exe")
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
