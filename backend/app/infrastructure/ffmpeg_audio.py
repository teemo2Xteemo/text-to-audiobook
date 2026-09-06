from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from app.domain.audio import AudioArtifact
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat
from app.infrastructure.ffmpeg import (
    concat_argv,
    normalize_argv,
    resolve_ffmpeg_executable,
    write_concat_list,
)


class FFmpegAudioProcessor:
    """Real FFmpeg normalize/merge for non-fake TTS (argv list only)."""

    def __init__(self, *, executable: str | None = None) -> None:
        self._executable = executable

    async def normalize(
        self,
        source: Path,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        destination.parent.mkdir(parents=True, exist_ok=True)
        argv = normalize_argv(
            source,
            destination,
            output_format=output_format,
            bitrate_kbps=bitrate_kbps,
            executable=self._resolved_executable(),
        )
        await run_ffmpeg_argv(argv)
        return AudioArtifact(path=destination)

    async def merge(
        self,
        sources: Sequence[Path],
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        del output_format, bitrate_kbps
        destination.parent.mkdir(parents=True, exist_ok=True)
        list_file = destination.parent / (destination.name + ".concat.txt")
        write_concat_list(list_file, sources)
        argv = concat_argv(
            list_file,
            destination,
            executable=self._resolved_executable(),
        )
        await run_ffmpeg_argv(argv)
        return AudioArtifact(path=destination)

    def _resolved_executable(self) -> str:
        if self._executable is not None:
            return self._executable
        found = resolve_ffmpeg_executable()
        if found is None:
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "ffmpeg is not available")
        return found


async def run_ffmpeg_argv(argv: Sequence[str]) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "ffmpeg is not available") from exc
    await process.communicate()
    if process.returncode != 0:
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "ffmpeg failed")
