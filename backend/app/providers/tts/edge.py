from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.chunking import chunk_text
from app.domain.errors import DomainError, ErrorType

logger = logging.getLogger(__name__)


class EdgeTtsClient(Protocol):
    def list_voices(self) -> Sequence[Mapping[str, Any]]: ...

    async def synthesize(self, text: str, voice: str, rate: str, output_path: Path) -> None: ...


_TTS_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f]")
_TTS_UNICODE_SPACE_RE = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")


def _clean_tts_text(text: str) -> str:
    cleaned = _TTS_INVISIBLE_CHARS_RE.sub("", text)
    cleaned = _TTS_UNICODE_SPACE_RE.sub(" ", cleaned)
    lines = [re.sub(r" +", " ", line.strip()) for line in cleaned.splitlines()]
    non_empty = [line for line in lines if line]
    return "\n".join(non_empty)


def speed_to_edge_rate(speed: float) -> str:
    percent = int(round((speed - 1.0) * 100))
    return f"{percent:+d}%"


DEFAULT_EDGE_TTS_TIMEOUT_SECONDS = 90.0
DEFAULT_EDGE_TTS_MAX_CHARS = 500
# In-client attempts for fast failures only (NoAudioReceived, empty output). Timeouts are
# not retried here: the orchestrator RetryPolicy owns them, so one chunk is bounded by
# RETRY_MAX_ATTEMPTS * EDGE_TTS_TIMEOUT_SECONDS.
_EDGE_MAX_ATTEMPTS = 2
_EDGE_RETRY_BACKOFF_SECONDS = 2.0
_EDGE_SEGMENT_PAUSE_SECONDS = 0.3


class EdgeTTSProvider:
    """Edge TTS adapter registered as ``TTS_PROVIDER=edge``."""

    max_concurrency: int = 2

    def __init__(
        self,
        output_dir: Path,
        *,
        default_voice_by_language: Mapping[str, str] | None = None,
        client: EdgeTtsClient | None = None,
        max_chars: int = DEFAULT_EDGE_TTS_MAX_CHARS,
        timeout_seconds: float = DEFAULT_EDGE_TTS_TIMEOUT_SECONDS,
    ) -> None:
        self._output_dir = output_dir
        self._defaults = dict(default_voice_by_language or {})
        self._max_chars = max_chars
        self._timeout_seconds = timeout_seconds
        self._client = (
            client
            if client is not None
            else SdkEdgeTtsClient(timeout_seconds=timeout_seconds, max_chars=max_chars)
        )
        self._voices: list[Voice] | None = None
        self._voices_by_language: dict[str, list[Voice]] | None = None

    def voices_for(self, language: str) -> Sequence[Voice]:
        if self._voices_by_language is None:
            indexed: dict[str, list[Voice]] = {}
            for voice in self._all_voices():
                indexed.setdefault(voice.language, []).append(voice)
            self._voices_by_language = indexed
        matched = self._voices_by_language.get(language, [])
        preferred_id = self._defaults.get(language)
        if not preferred_id:
            return matched
        preferred = [voice for voice in matched if voice.id == preferred_id]
        if not preferred:
            return matched
        rest = [voice for voice in matched if voice.id != preferred_id]
        return preferred + rest

    def languages(self) -> set[str]:
        return {v.language for v in self._all_voices()}

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        if language:
            available = {item.id for item in self.voices_for(language)}
            if voice not in available:
                lang_prefix = language.split("-")[0].lower()
                family_voices = {
                    item.id
                    for item in self._all_voices()
                    if item.language.split("-")[0].lower() == lang_prefix
                }
                if voice not in family_voices:
                    raise DomainError(
                        ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language"
                    )
        else:
            all_voice_ids = {item.id for item in self._all_voices()}
            if voice not in all_voice_ids:
                raise DomainError(
                    ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language"
                )
        cleaned_text = _clean_tts_text(text)
        if not cleaned_text:
            raise DomainError(ErrorType.INVALID_INPUT, "text is required")
        rate = speed_to_edge_rate(settings.speed)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"edge-{uuid4().hex}.mp3"
        logger.info(
            "edge_synthesize",
            extra={
                "provider": "edge",
                "language": language,
                "voice": voice,
                "character_count": len(cleaned_text),
            },
        )
        try:
            await self._client.synthesize(cleaned_text, voice, rate, path)
        except DomainError:
            path.unlink(missing_ok=True)
            raise
        except Exception as exc:
            path.unlink(missing_ok=True)
            logger.warning("edge_synthesize_failed: %s (%s)", exc, type(exc).__name__)
            raise _map_edge_error(exc) from exc
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if not path.is_file() or path.stat().st_size == 0:
            path.unlink(missing_ok=True)
            raise DomainError(ErrorType.TTS_FAILED, "tts failed")
        return AudioArtifact(path=path)

    def all_voices(self) -> Sequence[Voice]:
        return self._all_voices()

    def _all_voices(self) -> list[Voice]:
        if self._voices is None:
            try:
                records = self._client.list_voices()
            except DomainError:
                raise
            except Exception as exc:
                raise _map_edge_error(exc) from exc
            self._voices = [_to_voice(record) for record in records if _usable_record(record)]
        return self._voices


class SdkEdgeTtsClient:
    """Lazy ``edge_tts`` wrapper. Imported only when listing or synthesizing."""

    def __init__(
        self,
        timeout_seconds: float = DEFAULT_EDGE_TTS_TIMEOUT_SECONDS,
        max_chars: int = DEFAULT_EDGE_TTS_MAX_CHARS,
        retry_backoff_seconds: float = _EDGE_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_chars = max(50, max_chars)
        self._retry_backoff_seconds = retry_backoff_seconds

    def list_voices(self) -> Sequence[Mapping[str, Any]]:
        import edge_tts

        return list(_run_sync(edge_tts.list_voices()))

    async def synthesize(self, text: str, voice: str, rate: str, output_path: Path) -> None:
        # One deadline covers every segment and retry of this chunk.
        async with asyncio.timeout(self._timeout_seconds):
            await self._synthesize_segments(text, voice, rate, output_path)

    async def _synthesize_segments(
        self, text: str, voice: str, rate: str, output_path: Path
    ) -> None:
        segments = (
            [c.text for c in chunk_text(text, max_chars=self._max_chars)]
            if len(text) > self._max_chars
            else [text]
        )
        if len(segments) <= 1:
            await self._synthesize_single(text, voice, rate, output_path)
            return

        temp_paths: list[Path] = []
        try:
            for idx, segment in enumerate(segments):
                if idx:
                    await asyncio.sleep(_EDGE_SEGMENT_PAUSE_SECONDS)
                part_path = output_path.parent / f"{output_path.stem}.part-{idx:03d}.tmp"
                temp_paths.append(part_path)
                await self._synthesize_single(segment, voice, rate, part_path)

            output_path.unlink(missing_ok=True)
            with output_path.open("wb") as out_f:
                for part_path in temp_paths:
                    with part_path.open("rb") as in_f:
                        shutil.copyfileobj(in_f, out_f)
        finally:
            for part_path in temp_paths:
                part_path.unlink(missing_ok=True)

    async def _synthesize_single(self, text: str, voice: str, rate: str, output_path: Path) -> None:
        import edge_tts

        no_audio_exc = getattr(getattr(edge_tts, "exceptions", None), "NoAudioReceived", None)
        retry_exceptions: tuple[type[BaseException], ...] = (
            (no_audio_exc, _EmptyAudioError) if no_audio_exc else (_EmptyAudioError,)
        )

        for attempt in range(_EDGE_MAX_ATTEMPTS):
            output_path.unlink(missing_ok=True)
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            try:
                await communicate.save(str(output_path))
                if not output_path.is_file() or output_path.stat().st_size == 0:
                    raise _EmptyAudioError()
                return
            except retry_exceptions as exc:
                output_path.unlink(missing_ok=True)
                if attempt + 1 >= _EDGE_MAX_ATTEMPTS:
                    raise DomainError(
                        ErrorType.TTS_FAILED,
                        "tts failed: no audio received from speech service",
                        retry_after_seconds=3.0,
                    ) from exc
                wait_seconds = self._retry_backoff_seconds * (attempt + 1)
                logger.warning(
                    "edge_tts_client_retry: attempt %d/%d failed with %s, retrying in %.1fs",
                    attempt + 1,
                    _EDGE_MAX_ATTEMPTS,
                    type(exc).__name__,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
            except BaseException:
                output_path.unlink(missing_ok=True)
                raise


class _EmptyAudioError(Exception):
    """Edge returned without writing any audio bytes."""


def _to_voice(record: Mapping[str, Any]) -> Voice:
    short_name = str(record.get("ShortName", ""))
    locale = str(record.get("Locale", ""))
    label = str(record.get("FriendlyName") or short_name)
    return Voice(id=short_name, language=locale, label=label)


def _usable_record(record: Mapping[str, Any]) -> bool:
    return bool(str(record.get("ShortName", "")).strip() and str(record.get("Locale", "")).strip())


def _map_edge_error(exc: BaseException) -> DomainError:
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status == 429:
        return DomainError(
            ErrorType.PROVIDER_RATE_LIMIT, "tts rate limited", retry_after_seconds=5.0
        )
    if isinstance(exc, TimeoutError):
        return DomainError(ErrorType.TIMEOUT, "tts timed out", retry_after_seconds=3.0)
    exc_name = type(exc).__name__
    if exc_name == "NoAudioReceived":
        return DomainError(
            ErrorType.TTS_FAILED,
            "tts failed: no audio received from speech service",
            retry_after_seconds=3.0,
        )
    return DomainError(
        ErrorType.TTS_FAILED,
        f"tts failed: {exc}" if str(exc) else "tts failed",
        retry_after_seconds=2.0,
    )


def _run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
