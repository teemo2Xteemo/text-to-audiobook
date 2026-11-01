from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.errors import DomainError, ErrorType
from app.providers.tts.brackets import sanitize_brackets
from app.providers.tts.http_errors import map_tts_http_error

VOICE_PREFIX = "fish:"


def parse_fish_voices(value: str) -> list[Voice]:
    """Parse ``lang=reference_id[:label],...`` into namespaced voices."""
    voices: list[Voice] = []
    for item in value.split(","):
        lang, sep, rest = item.strip().partition("=")
        ref, _, label = rest.partition(":")
        lang, ref, label = lang.strip(), ref.strip(), label.strip()
        if not (sep and lang and ref):
            continue
        voices.append(Voice(id=f"{VOICE_PREFIX}{ref}", language=lang, label=label or ref))
    return voices


class FishTTSProvider:
    """Fish Audio TTS adapter registered as ``TTS_PROVIDER=fish``."""

    def __init__(
        self,
        output_dir: Path,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        voices: Sequence[Voice],
        timeout_seconds: float,
        max_concurrency: int,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._api_key = (api_key or "").strip()
        self._url = base_url.rstrip("/") + "/v1/tts"
        self._model = model
        self._voices = list(voices)
        self._timeout = timeout_seconds
        self.max_concurrency = max_concurrency
        self._opener = opener or urllib.request.urlopen

    async def check_available(self) -> None:
        if not self._api_key:
            raise DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR, "FISH_AUDIO_API_KEY is not set"
            )

    def all_voices(self) -> Sequence[Voice]:
        return list(self._voices)

    def voices_for(self, language: str) -> Sequence[Voice]:
        return [v for v in self._voices if v.language == language]

    def languages(self) -> set[str]:
        return {v.language for v in self._voices}

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        pool = self.voices_for(language) if language else self._voices
        if voice not in {v.id for v in pool}:
            raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language")
        cleaned = sanitize_brackets(text)
        if not cleaned:
            raise DomainError(ErrorType.INVALID_INPUT, "text is required")
        await self.check_available()
        body = {
            "text": cleaned,
            "reference_id": voice[len(VOICE_PREFIX) :],
            "format": "mp3",
            "mp3_bitrate": 128,
            "normalize": True,
            "prosody": {"speed": settings.speed},
        }
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "model": self._model,
            },
        )
        try:
            audio = await asyncio.to_thread(self._post, request)
        except Exception as exc:
            raise map_tts_http_error(exc) from exc
        if not audio:
            raise DomainError(ErrorType.TTS_FAILED, "tts returned empty audio")
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"fish-{uuid4().hex}.mp3"
        path.write_bytes(audio)
        return AudioArtifact(path=path)

    def _post(self, request: urllib.request.Request) -> bytes:
        try:
            with self._opener(request, timeout=self._timeout) as response:
                data: bytes = response.read()
                return data
        except urllib.error.HTTPError as exc:
            exc.close()
            raise
