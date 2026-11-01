from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.errors import DomainError, ErrorType
from app.providers.tts.brackets import sanitize_brackets
from app.providers.tts.http_errors import map_tts_http_error, open_bytes

logger = logging.getLogger(__name__)

VOICE_PREFIX = "vieneu:"
LANGUAGE = "vi-VN"
PROBE_TIMEOUT_SECONDS = 5.0  # voices/health probes; synthesis keeps its own long timeout
CAPABILITIES_BACKOFF_SECONDS = 30.0  # after a failed voices fetch; chunk retries pass 0


def _voice_names(payload: Any) -> list[str]:
    items = payload.get("voices", payload.get("data", [])) if isinstance(payload, dict) else payload
    names: list[str] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            item = item.get("id") or item.get("name") or item.get("voice")
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names


class VieneuTTSProvider:
    """VieNeu-TTS sidecar adapter (OpenAI-style speech API), ``TTS_PROVIDER=vieneu``."""

    # Upstream does not document `speed`; the orchestrator applies a tempo fallback.
    honours_speed = False

    def __init__(
        self,
        output_dir: Path,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_concurrency: int = 1,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._base = base_url.rstrip("/")
        self._root = self._base.removesuffix("/v1")
        self._model = model
        self._timeout = timeout_seconds
        self.max_concurrency = max_concurrency
        self._opener = opener or urllib.request.urlopen
        self._voices: list[Voice] | None = None
        self._failed_at: float | None = None

    async def check_available(self) -> None:
        request = urllib.request.Request(f"{self._root}/health", method="GET")
        try:
            await asyncio.to_thread(open_bytes, self._opener, request, PROBE_TIMEOUT_SECONDS)
        except Exception as exc:
            raise DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR, "VieNeu-TTS sidecar is not reachable"
            ) from exc
        await self.refresh_voices()

    async def refresh_voices(
        self, *, backoff_seconds: float = CAPABILITIES_BACKOFF_SECONDS
    ) -> None:
        """Fetch ``/voices`` off the event loop; never raises. Failed or empty fetches are
        not retried until ``backoff_seconds`` after the failed probe returned."""
        if self._voices is not None:
            return
        if self._failed_at is not None and time.monotonic() - self._failed_at < backoff_seconds:
            return
        request = urllib.request.Request(f"{self._base}/voices", method="GET")
        try:
            raw = await asyncio.to_thread(open_bytes, self._opener, request, PROBE_TIMEOUT_SECONDS)
            names = _voice_names(json.loads(raw))
        except Exception:
            logger.warning("vieneu_voices_unavailable", exc_info=True)
            names = []
        if not names:
            self._failed_at = time.monotonic()
            return
        self._voices = [Voice(id=f"{VOICE_PREFIX}{n}", language=LANGUAGE, label=n) for n in names]

    def all_voices(self) -> Sequence[Voice]:
        """Cached voices only (no I/O); ``refresh_voices`` fills the cache."""
        return list(self._voices or [])

    def voices_for(self, language: str) -> Sequence[Voice]:
        return [v for v in self.all_voices() if v.language == language]

    def languages(self) -> set[str]:
        return {LANGUAGE}

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        if language not in ("", LANGUAGE) or not voice.startswith(VOICE_PREFIX):
            raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language")
        cleaned = sanitize_brackets(text)
        if not cleaned:
            raise DomainError(ErrorType.INVALID_INPUT, "text is required")
        body = {
            "model": self._model,
            "input": cleaned,
            "voice": voice[len(VOICE_PREFIX) :],
            "response_format": "wav",
            "speed": settings.speed,
        }
        request = urllib.request.Request(
            f"{self._base}/audio/speech",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            audio = await asyncio.to_thread(open_bytes, self._opener, request, self._timeout)
        except Exception as exc:
            raise map_tts_http_error(exc) from exc
        if not audio:
            raise DomainError(ErrorType.TTS_FAILED, "tts returned empty audio")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"vieneu-{uuid4().hex}.wav"
        path.write_bytes(audio)
        return AudioArtifact(path=path)
