from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.errors import DomainError, ErrorType

logger = logging.getLogger(__name__)


class EdgeTtsClient(Protocol):
    def list_voices(self) -> Sequence[Mapping[str, Any]]: ...

    async def synthesize(self, text: str, voice: str, rate: str, output_path: Path) -> None: ...


def speed_to_edge_rate(speed: float) -> str:
    percent = int(round((speed - 1.0) * 100))
    return f"{percent:+d}%"


class EdgeTTSProvider:
    """Edge TTS adapter registered as ``TTS_PROVIDER=edge``."""

    def __init__(
        self,
        output_dir: Path,
        *,
        default_voice_by_language: Mapping[str, str] | None = None,
        client: EdgeTtsClient | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._defaults = dict(default_voice_by_language or {})
        self._client = client if client is not None else SdkEdgeTtsClient()
        self._voices: list[Voice] | None = None

    def voices_for(self, language: str) -> Sequence[Voice]:
        matched = [voice for voice in self._all_voices() if voice.language == language]
        preferred_id = self._defaults.get(language)
        if not preferred_id:
            return matched
        preferred = [voice for voice in matched if voice.id == preferred_id]
        if not preferred:
            return matched
        rest = [voice for voice in matched if voice.id != preferred_id]
        return preferred + rest

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        available = {item.id for item in self.voices_for(language)}
        if voice not in available:
            raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language")
        rate = speed_to_edge_rate(settings.speed)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"edge-{uuid4().hex}.mp3"
        logger.info(
            "edge_synthesize",
            extra={
                "provider": "edge",
                "language": language,
                "voice": voice,
                "character_count": len(text),
            },
        )
        try:
            await self._client.synthesize(text, voice, rate, path)
        except DomainError:
            raise
        except Exception as exc:
            raise _map_edge_error(exc) from exc
        if not path.is_file() or path.stat().st_size == 0:
            raise DomainError(ErrorType.TTS_FAILED, "tts failed")
        return AudioArtifact(path=path)

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

    def list_voices(self) -> Sequence[Mapping[str, Any]]:
        import edge_tts

        return list(_run_sync(edge_tts.list_voices()))

    async def synthesize(self, text: str, voice: str, rate: str, output_path: Path) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(str(output_path))


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
        return DomainError(ErrorType.PROVIDER_RATE_LIMIT, "tts rate limited")
    if isinstance(exc, TimeoutError):
        return DomainError(ErrorType.TIMEOUT, "tts timed out")
    return DomainError(ErrorType.TTS_FAILED, "tts failed")


def _run_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
