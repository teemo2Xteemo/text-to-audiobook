from __future__ import annotations

from pathlib import Path

from redis import Redis
from rq import Queue

from app.application.capabilities import CapabilitiesService
from app.application.jobs import JobService
from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.application.pipeline.passthrough import (
    FixedLanguageDetector,
    PassthroughNarrationProcessor,
)
from app.config.settings import Settings
from app.domain.ports import AudioProcessor, TranslationProvider, TTSProvider
from app.infrastructure.fake_audio import FakeAudioProcessor
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
        narration=PassthroughNarrationProcessor(),
        detector=FixedLanguageDetector(),
        audio=build_audio_processor(settings),
        jobs=store,
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
    raise UnknownProviderError(f"unknown TRANSLATION_PROVIDER: {settings.translation_provider}")


def build_tts_provider(settings: Settings) -> TTSProvider:
    name = settings.tts_provider.strip().lower()
    if name == "fake":
        return FakeTTSProvider(output_dir=_tts_tmp_dir(settings.storage_path))
    raise UnknownProviderError(f"unknown TTS_PROVIDER: {settings.tts_provider}")


def build_audio_processor(settings: Settings) -> AudioProcessor:
    # Fake TTS emits non-media bytes; real FFmpeg lands in M9 with Edge.
    if settings.tts_provider.strip().lower() == "fake":
        return FakeAudioProcessor()
    raise UnknownProviderError(
        f"no AudioProcessor registered for TTS_PROVIDER={settings.tts_provider}"
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
