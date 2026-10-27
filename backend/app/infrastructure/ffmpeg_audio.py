from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from pathlib import Path

from app.domain.audio import AudioArtifact
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat
from app.infrastructure.ffmpeg import (
    concat_argv,
    ffprobe_duration_argv,
    normalize_argv,
    resolve_ffmpeg_executable,
    resolve_ffprobe_executable,
    silence_argv,
    tempo_argv,
    write_concat_list,
)

logger = logging.getLogger(__name__)


class FFmpegAudioProcessor:
    """Real FFmpeg normalize/merge/silence/duration/tempo for non-fake TTS."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        ffprobe_executable: str | None = None,
    ) -> None:
        self._executable = executable
        self._ffprobe_executable = ffprobe_executable

    async def normalize(
        self,
        source: Path,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        if not source.is_file():
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, f"Audio file not found: {source}")
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
        if not sources:
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "cannot merge empty audio sources")
        for src in sources:
            if not src.is_file():
                raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, f"Audio file not found: {src}")
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

    async def get_duration_ms(self, path: Path) -> int:
        if not path.is_file():
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, f"Audio file not found: {path}")
        argv = ffprobe_duration_argv(path, executable=self._resolved_ffprobe_executable())
        output = await run_ffprobe_argv(argv)
        try:
            sec = float(output)
        except ValueError as exc:
            raise DomainError(
                ErrorType.AUDIO_PROCESSING_FAILED,
                f"ffprobe returned non-numeric duration: {output!r}",
            ) from exc
        return int(round(sec * 1000))

    async def generate_silence(
        self,
        duration_ms: int,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        destination.parent.mkdir(parents=True, exist_ok=True)
        argv = silence_argv(
            duration_ms,
            destination,
            output_format=output_format,
            bitrate_kbps=bitrate_kbps,
            executable=self._resolved_executable(),
        )
        await run_ffmpeg_argv(argv)
        return AudioArtifact(path=destination)

    async def adjust_tempo(
        self,
        source: Path,
        destination: Path,
        factor: float,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        if not source.is_file():
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, f"Audio file not found: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            argv = tempo_argv(
                source,
                destination,
                factor,
                output_format=output_format,
                bitrate_kbps=bitrate_kbps,
                executable=self._resolved_executable(),
            )
        except ValueError as exc:
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, str(exc)) from exc
        await run_ffmpeg_argv(argv)
        return AudioArtifact(path=destination)

    def _resolved_executable(self) -> str:
        if self._executable is not None:
            return self._executable
        found = resolve_ffmpeg_executable()
        if found is None:
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "ffmpeg is not available")
        return found

    def _resolved_ffprobe_executable(self) -> str:
        if self._ffprobe_executable is not None:
            return self._ffprobe_executable
        found = resolve_ffprobe_executable()
        if found is None:
            if self._executable is not None:
                candidate = Path(self._executable).with_name("ffprobe")
                if candidate.is_file():
                    return str(candidate)
                candidate_exe = Path(self._executable).with_name("ffprobe.exe")
                if candidate_exe.is_file():
                    return str(candidate_exe)
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "ffprobe is not available")
        return found


_PATH_PATTERN = re.compile(r"(?:/|[a-zA-Z]:[\\/])(?:[^\s:?*\"<>|\r\n]+[\\/])+[^\s:?*\"<>|\r\n]+")
_POINTER_PATTERN = re.compile(r"\b0x[0-9a-fA-F]+\b")


def sanitize_ffmpeg_stderr(stderr_text: str) -> str:
    """Sanitize FFmpeg/FFprobe stderr for client-facing error messages.

    Redacts absolute filesystem paths and memory addresses to prevent information leakage,
    while preserving the operational error diagnosis.
    """
    if not stderr_text:
        return ""
    cleaned = _POINTER_PATTERN.sub("<addr>", stderr_text)
    cleaned = _PATH_PATTERN.sub("<path>", cleaned)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) >= 2 and len(f"{lines[-2]} · {lines[-1]}") <= 200:
        return f"{lines[-2]} · {lines[-1]}"
    return lines[-1][:200]


DEFAULT_FFMPEG_TIMEOUT_SECONDS = 120.0
DEFAULT_FFPROBE_TIMEOUT_SECONDS = 30.0


async def run_ffmpeg_argv(
    argv: Sequence[str],
    timeout_seconds: float = DEFAULT_FFMPEG_TIMEOUT_SECONDS,
) -> None:
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "ffmpeg is not available") from exc
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        extra_ctx = {"argv": list(argv), "timeout": timeout_seconds}
        logger.error("ffmpeg_timed_out", extra=extra_ctx)
        msg_timeout = "ffmpeg timed out after " + str(int(timeout_seconds)) + "s"
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, msg_timeout) from None
    if process.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        extra_fail = {"argv": list(argv), "stderr": err_msg}
        logger.error("ffmpeg_execution_failed", extra=extra_fail)
        sanitized = sanitize_ffmpeg_stderr(err_msg)
        msg = ("ffmpeg failed: " + sanitized) if sanitized else "ffmpeg failed"
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, msg)


async def run_ffprobe_argv(
    argv: Sequence[str],
    timeout_seconds: float = DEFAULT_FFPROBE_TIMEOUT_SECONDS,
) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "ffprobe is not available") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        extra_ctx = {"argv": list(argv), "timeout": timeout_seconds}
        logger.error("ffprobe_timed_out", extra=extra_ctx)
        msg_timeout = "ffprobe timed out after " + str(int(timeout_seconds)) + "s"
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, msg_timeout) from None
    if process.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        extra_fail = {"argv": list(argv), "stderr": err_msg}
        logger.error("ffprobe_execution_failed", extra=extra_fail)
        sanitized = sanitize_ffmpeg_stderr(err_msg)
        msg = ("ffprobe failed: " + sanitized) if sanitized else "ffprobe failed"
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, msg)
    return stdout.decode("utf-8", errors="replace").strip()
