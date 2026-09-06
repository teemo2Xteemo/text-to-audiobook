from __future__ import annotations

from pathlib import Path

from redis import Redis
from rq import Queue

from app.application.capabilities import CapabilitiesService
from app.application.jobs import JobService
from app.application.pipeline.artifact_cache import CacheIdentity, PipelineArtifactCache
from app.application.pipeline.conservative_narration import ConservativeNarrationProcessor
from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.config.settings import Settings, parse_tts_default_voice_by_language
from app.domain.ports import (
    AudioProcessor,
    LanguageDetector,
    NarrationProcessor,
    TranslationProvider,
    TTSProvider,
)
from app.domain.retry import RetryPolicy
from app.infrastructure.artifact_cache_fs import FilesystemArtifactCache
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.infrastructure.ffmpeg_audio import FFmpegAudioProcessor
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore
from app.infrastructure.redis_job_store import RedisJobCache
from app.infrastructure.rq_queue import RQ_QUEUE_NAME, RQJobQueue
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.tts.fake import FakeTTSProvider


class UnknownProviderError(ValueError):
    """Raised when ``TRANSLATION_PROVIDER`` or ``TTS_PROVIDER`` is not registered."""


def build_job_service(settings: Settings) -> JobService:
    filesystem, store, queue = _infrastructure(settings)
    return JobService(
        jobs=store,
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=settings.output_bitrate_kbps,
        storage_path=settings.storage_path,
    )


def build_capabilities_service(settings: Settings) -> CapabilitiesService:
    translation = build_translation_provider(settings)
    tts = build_tts_provider(settings)
    return CapabilitiesService(translation=translation, tts=tts)


def build_orchestrator(settings: Settings) -> PipelineOrchestrator:
    filesystem, store, _queue = _infrastructure(settings)
    del filesystem
    return PipelineOrchestrator(
        translation=build_translation_provider(settings),
        tts=build_tts_provider(settings),
        narration=build_narration_processor(),
        detector=build_language_detector(settings),
        audio=build_audio_processor(settings),
        jobs=store,
        artifact_cache=build_artifact_cache(settings),
        retry_policy=RetryPolicy(
            max_attempts=settings.retry_max_attempts,
            backoff_seconds=settings.retry_backoff_seconds,
        ),
    )


def build_job_store(settings: Settings) -> DualWriteJobStore:
    _filesystem, store, _queue = _infrastructure(settings)
    return store


def build_source_storage(settings: Settings) -> FilesystemJobStorage:
    filesystem, _store, _queue = _infrastructure(settings)
    return filesystem


def build_translation_provider(settings: Settings) -> TranslationProvider:
    name = settings.translation_provider.strip().lower()
    if name == "fake":
        return FakeTranslationProvider()
    if name == "nllb":
        from app.providers.translation.nllb import NllbTranslationProvider

        return NllbTranslationProvider(model_id=settings.nllb_model_id)
    raise UnknownProviderError(f"unknown TRANSLATION_PROVIDER: {settings.translation_provider}")


def build_language_detector(settings: Settings) -> LanguageDetector:
    from app.providers.language_detection.cpu import CpuLanguageDetector

    return CpuLanguageDetector(min_confidence=settings.language_detect_min_confidence)


def build_tts_provider(settings: Settings) -> TTSProvider:
    name = settings.tts_provider.strip().lower()
    if name == "fake":
        return FakeTTSProvider(output_dir=_tts_tmp_dir(settings.storage_path))
    if name == "edge":
        from app.providers.tts.edge import EdgeTTSProvider

        return EdgeTTSProvider(
            output_dir=_tts_tmp_dir(settings.storage_path),
            default_voice_by_language=parse_tts_default_voice_by_language(
                settings.tts_default_voice_by_language
            ),
        )
    raise UnknownProviderError(f"unknown TTS_PROVIDER: {settings.tts_provider}")


def build_narration_processor() -> NarrationProcessor:
    return ConservativeNarrationProcessor()


def build_audio_processor(settings: Settings) -> AudioProcessor:
    # Fake TTS emits non-media bytes; Edge (and later real TTS) needs FFmpeg normalize/merge.
    if settings.tts_provider.strip().lower() == "fake":
        return FakeAudioProcessor()
    return FFmpegAudioProcessor()


def build_artifact_cache(settings: Settings) -> PipelineArtifactCache:
    return PipelineArtifactCache(
        FilesystemArtifactCache(settings.storage_path / "cache"),
        cache_identity_from_settings(settings),
    )


def cache_identity_from_settings(settings: Settings) -> CacheIdentity:
    translation = settings.translation_provider.strip().lower()
    tts = settings.tts_provider.strip().lower()
    translation_model = settings.nllb_model_id if translation == "nllb" else "fake"
    return CacheIdentity(
        translation_provider=translation,
        translation_model=translation_model,
        tts_provider=tts,
        tts_model=tts,
    )


def _infrastructure(
    settings: Settings,
) -> tuple[FilesystemJobStorage, DualWriteJobStore, RQJobQueue]:
    cache_client = Redis.from_url(str(settings.redis_url), decode_responses=True)
    queue_client = Redis.from_url(str(settings.redis_url))
    filesystem = FilesystemJobStorage(settings.storage_path)
    store = DualWriteJobStore(filesystem, RedisJobCache(cache_client))
    queue = RQJobQueue(Queue(RQ_QUEUE_NAME, connection=queue_client))
    return filesystem, store, queue


def _tts_tmp_dir(storage_path: Path) -> Path:
    return storage_path / ".provider-tmp" / "tts"
