from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.domain.audio import TTSSettings
from app.domain.cache import build_cache_key
from app.domain.errors import DomainError
from app.domain.ports import ArtifactCache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheIdentity:
    translation_provider: str
    translation_model: str
    tts_provider: str
    tts_model: str


class PipelineArtifactCache:
    """Lookup policy: hash with ``build_cache_key``, copy via ``ArtifactCache``."""

    def __init__(self, store: ArtifactCache, identity: CacheIdentity) -> None:
        self._store = store
        self._identity = identity

    def fill_translation(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        destination: Path,
        job_id: str,
        chunk_id: str,
    ) -> bool:
        key = build_cache_key(
            operation="translation",
            text=text,
            source_language=source_language,
            target_language=target_language,
            provider=self._identity.translation_provider,
            model=self._identity.translation_model,
            voice="",
            settings={},
        )
        return self._fill(
            operation="translation",
            key=key,
            destination=destination,
            job_id=job_id,
            chunk_id=chunk_id,
        )

    def store_translation(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        source: Path,
        job_id: str,
        chunk_id: str,
    ) -> None:
        key = build_cache_key(
            operation="translation",
            text=text,
            source_language=source_language,
            target_language=target_language,
            provider=self._identity.translation_provider,
            model=self._identity.translation_model,
            voice="",
            settings={},
        )
        self._store_blob(
            operation="translation",
            key=key,
            source=source,
            job_id=job_id,
            chunk_id=chunk_id,
        )

    def fill_tts(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        voice: str,
        settings: TTSSettings,
        destination: Path,
        job_id: str,
        chunk_id: str,
    ) -> bool:
        key = self._tts_key(
            text=text,
            source_language=source_language,
            target_language=target_language,
            voice=voice,
            settings=settings,
        )
        return self._fill(
            operation="tts",
            key=key,
            destination=destination,
            job_id=job_id,
            chunk_id=chunk_id,
        )

    def store_tts(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        voice: str,
        settings: TTSSettings,
        source: Path,
        job_id: str,
        chunk_id: str,
    ) -> None:
        key = self._tts_key(
            text=text,
            source_language=source_language,
            target_language=target_language,
            voice=voice,
            settings=settings,
        )
        self._store_blob(
            operation="tts",
            key=key,
            source=source,
            job_id=job_id,
            chunk_id=chunk_id,
        )

    def _tts_key(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        voice: str,
        settings: TTSSettings,
    ) -> str:
        return build_cache_key(
            operation="tts",
            text=text,
            source_language=source_language,
            target_language=target_language,
            provider=self._identity.tts_provider,
            model=self._identity.tts_model,
            voice=voice,
            settings=settings,
        )

    def _fill(
        self,
        *,
        operation: str,
        key: str,
        destination: Path,
        job_id: str,
        chunk_id: str,
    ) -> bool:
        try:
            hit = self._store.get(operation, key, destination)
        except (DomainError, OSError):
            hit = False
        extra = {"job_id": job_id, "chunk_id": chunk_id, "operation": operation}
        if hit:
            logger.info("cache_hit", extra=extra)
            return True
        logger.info("cache_miss", extra=extra)
        return False

    def _store_blob(
        self,
        *,
        operation: str,
        key: str,
        source: Path,
        job_id: str,
        chunk_id: str,
    ) -> None:
        try:
            self._store.put(operation, key, source)
        except (DomainError, OSError):
            logger.warning(
                "cache_put_failed",
                extra={"job_id": job_id, "chunk_id": chunk_id, "operation": operation},
            )
