from __future__ import annotations

import importlib.util
import logging
from dataclasses import replace
from pathlib import Path

from redis import Redis
from rq import Queue

from app.application.capabilities import CapabilitiesService
from app.application.jobs import JobService
from app.application.pipeline.artifact_cache import CacheIdentity, PipelineArtifactCache
from app.application.pipeline.conservative_narration import ConservativeNarrationProcessor
from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.config.settings import Settings, parse_tts_default_voice_by_language
from app.domain.jobs import Job, JobMode
from app.domain.ports import (
    AudioProcessor,
    LanguageDetector,
    NarrationProcessor,
    NullTranslationProvider,
    TranslationProvider,
    TTSProvider,
)
from app.domain.retry import RetryPolicy
from app.domain.translation_profile import TranslationProfile
from app.infrastructure.artifact_cache_fs import FilesystemArtifactCache
from app.infrastructure.checkpoint_fs import CheckpointFilesystem
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.infrastructure.ffmpeg_audio import FFmpegAudioProcessor
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore
from app.infrastructure.redis_job_store import RedisJobCache
from app.infrastructure.rq_queue import RQ_QUEUE_NAME, RQJobQueue
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.tts.fake import FakeTTSProvider

logger = logging.getLogger(__name__)


class ProviderConfigurationError(ValueError):
    """Raised when provider configuration or required credentials are missing."""


class UnknownProviderError(ProviderConfigurationError):
    """Raised when ``TRANSLATION_PROVIDER`` or ``TTS_PROVIDER`` is not registered."""


def build_job_service(settings: Settings) -> JobService:
    filesystem, store, queue = _infrastructure(settings)
    provider, model = translation_pin_from_settings(settings)
    profiles = build_available_translation_profiles(settings)
    try:
        tts: TTSProvider | None = build_tts_provider(settings)
    except ProviderConfigurationError:
        tts = None
    return JobService(
        jobs=store,
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=settings.output_bitrate_kbps,
        storage_path=settings.storage_path,
        disk_jobs=filesystem,
        max_recovery_attempts=settings.max_recovery_attempts,
        translation_provider=provider,
        translation_model=model,
        available_profiles=profiles,
        tts=tts,
        detector=build_language_detector(settings),
    )


def _nllb_available() -> bool:
    try:
        return (
            importlib.util.find_spec("transformers") is not None
            and importlib.util.find_spec("sentencepiece") is not None
        )
    except (ImportError, ValueError, AttributeError):
        return False


def _languages_for_provider(provider: str) -> tuple[str, ...]:
    if provider in ("groq", "openrouter"):
        from app.providers.translation.openai_compatible import SUPPORTED_LANGUAGES

        return SUPPORTED_LANGUAGES
    if provider == "gemini":
        from app.providers.translation.gemini import SUPPORTED_LANGUAGES

        return SUPPORTED_LANGUAGES
    if provider == "nllb":
        from app.providers.translation.nllb import SUPPORTED_LANGUAGES

        return SUPPORTED_LANGUAGES
    if provider == "ollama":
        from app.providers.translation.ollama import SUPPORTED_LANGUAGES

        return SUPPORTED_LANGUAGES
    return ("en-US", "ja-JP", "ko-KR", "vi-VN")


def build_available_translation_profiles(settings: Settings) -> list[TranslationProfile]:
    name = settings.translation_provider.strip().lower()
    default_provider, default_model = translation_pin_from_settings(settings)
    if name == "fake":
        return [
            TranslationProfile(
                id="fake",
                provider="fake",
                model="fake",
                label="Fake Translation (Mock)",
                badge="MOCK",
                description="Mock translation for tests",
                is_default=True,
                supported_languages=("en-US", "ja-JP", "ko-KR", "vi-VN"),
            )
        ]

    profiles: list[TranslationProfile] = []

    gemini_key = (
        settings.gemini_api_key.get_secret_value().strip()
        if settings.gemini_api_key is not None
        else ""
    )
    if gemini_key:
        gemini_langs = _languages_for_provider("gemini")
        profiles.append(
            TranslationProfile(
                id="gemini-3.5-flash",
                provider="gemini",
                model="gemini-3.5-flash",
                label="Google Gemini 3.5 Flash",
                badge="ONLINE",
                description="Natural tone · Recommended",
                supported_languages=gemini_langs,
            )
        )
        profiles.append(
            TranslationProfile(
                id="gemini-2.0-flash",
                provider="gemini",
                model="gemini-2.0-flash",
                label="Google Gemini 2.0 Flash",
                badge="ONLINE",
                description="High speed · Saves quota",
                supported_languages=gemini_langs,
            )
        )

    groq_key = (
        settings.groq_api_key.get_secret_value().strip()
        if settings.groq_api_key is not None
        else ""
    )
    if groq_key:
        from app.providers.translation.openai_compatible import (
            GROQ_GPT_OSS_20B,
            GROQ_GPT_OSS_120B,
            GROQ_QWEN_MODEL,
        )

        groq_langs = _languages_for_provider("groq")
        profiles.append(
            TranslationProfile(
                id="groq-qwen",
                provider="groq",
                model=GROQ_QWEN_MODEL,
                label="Groq (Qwen 27B / Chinese-Vietnamese Specialist)",
                badge="ONLINE",
                description="Specialized for literary Asian stories · Fast",
                supported_languages=groq_langs,
            )
        )
        profiles.append(
            TranslationProfile(
                id="groq-gpt-oss-120b",
                provider="groq",
                model=GROQ_GPT_OSS_120B,
                label="Groq (GPT-OSS 120B)",
                badge="ONLINE",
                description="High capability · Natural flow",
                supported_languages=groq_langs,
            )
        )
        profiles.append(
            TranslationProfile(
                id="groq-gpt-oss-20b",
                provider="groq",
                model=GROQ_GPT_OSS_20B,
                label="Groq (GPT-OSS 20B)",
                badge="ONLINE",
                description="Fast · Higher daily limit",
                supported_languages=groq_langs,
            )
        )

    openrouter_key = (
        settings.openrouter_api_key.get_secret_value().strip()
        if settings.openrouter_api_key is not None
        else ""
    )
    if openrouter_key:
        from app.providers.translation.openai_compatible import OPENROUTER_DEEPSEEK_V4_FLASH

        openrouter_langs = _languages_for_provider("openrouter")
        profiles.append(
            TranslationProfile(
                id="openrouter-deepseek-v4-flash",
                provider="openrouter",
                model=OPENROUTER_DEEPSEEK_V4_FLASH,
                label="OpenRouter (DeepSeek V4 Flash)",
                badge="ONLINE",
                description="Fast · Reasoning off · Default when OpenRouter is selected",
                supported_languages=openrouter_langs,
            )
        )
        profiles.append(
            TranslationProfile(
                id="openrouter-auto",
                provider="openrouter",
                model="openrouter/auto",
                label="OpenRouter (Auto / Best Available)",
                badge="ONLINE",
                description="Auto-routes; served model can vary and may be paid",
                supported_languages=openrouter_langs,
            )
        )

    if _nllb_available():
        model_id = settings.nllb_model_id
        is_distilled_600m = "600M" in model_id or "600m" in model_id
        profile_id = "nllb-600m" if is_distilled_600m else f"nllb-{model_id.split('/')[-1]}"
        label = "NLLB-200 (CPU Offline)" if is_distilled_600m else f"NLLB-200 ({model_id})"
        profiles.append(
            TranslationProfile(
                id=profile_id,
                provider="nllb",
                model=model_id,
                label=label,
                badge="OFFLINE",
                description="Runs locally on CPU · Private",
                supported_languages=_languages_for_provider("nllb"),
            )
        )

    if name == "ollama" or default_provider == "ollama":
        profiles.append(
            TranslationProfile(
                id="ollama-gemma",
                provider="ollama",
                model=settings.ollama_translation_model,
                label="Ollama TranslateGemma",
                badge="LOCAL",
                description="Local open source model",
                supported_languages=_languages_for_provider("ollama"),
            )
        )

    # Ensure configured default model is always present in profiles
    matched_idx: int | None = None
    for idx, p in enumerate(profiles):
        if p.provider == default_provider and p.model == default_model:
            matched_idx = idx
            break

    if matched_idx is None and (default_provider != "nllb" or _nllb_available()):
        slug = default_model.split("/")[-1].replace(":", "-")
        default_id = slug if slug.startswith(default_provider) else f"{default_provider}-{slug}"
        badge = (
            "ONLINE"
            if default_provider in ("gemini", "groq", "openrouter")
            else ("OFFLINE" if default_provider == "nllb" else "LOCAL")
        )
        configured_profile = TranslationProfile(
            id=default_id,
            provider=default_provider,
            model=default_model,
            label=f"{default_provider.capitalize()} ({default_model})",
            badge=badge,
            description="Configured server default model",
            supported_languages=_languages_for_provider(default_provider),
        )
        profiles.insert(0, configured_profile)
        matched_idx = 0

    if default_provider == "gemini" and not gemini_key:
        logger.warning(
            "configured_default_provider_gemini_missing_api_key",
            extra={"provider": "gemini", "model": default_model},
        )
    elif default_provider == "groq" and not groq_key:
        logger.warning(
            "configured_default_provider_groq_missing_api_key",
            extra={"provider": "groq", "model": default_model},
        )
    elif default_provider == "openrouter" and not openrouter_key:
        logger.warning(
            "configured_default_provider_openrouter_missing_api_key",
            extra={"provider": "openrouter", "model": default_model},
        )
    elif default_provider == "nllb" and not _nllb_available():
        logger.warning(
            "configured_default_provider_nllb_missing_dependencies",
            extra={"provider": "nllb", "model": default_model},
        )

    return [replace(p, is_default=(i == matched_idx)) for i, p in enumerate(profiles)]


def build_capabilities_service(settings: Settings) -> CapabilitiesService:
    translation = build_translation_provider(settings)
    tts = build_tts_provider(settings)
    profiles = build_available_translation_profiles(settings)
    return CapabilitiesService(
        translation=translation,
        tts=tts,
        translation_profiles=profiles,
        estimate_defaults={
            "chars_per_second": float(settings.estimate_chars_per_second),
            "preview_wpm": float(settings.tts_preview_rate_per_minute),
        },
    )


def build_orchestrator(
    settings: Settings,
    *,
    for_mode: JobMode | None = None,
    translation_provider: TranslationProvider | None = None,
) -> PipelineOrchestrator:
    filesystem, store, _queue = _infrastructure(settings)
    del filesystem
    if translation_provider is not None:
        translation = translation_provider
    elif for_mode == JobMode.NARRATE:
        translation = NullTranslationProvider()
    else:
        translation = build_translation_provider(settings)
    return PipelineOrchestrator(
        translation=translation,
        tts=build_tts_provider(settings),
        narration=build_narration_processor(),
        detector=build_language_detector(settings),
        audio=build_audio_processor(settings),
        jobs=store,
        artifact_cache=build_artifact_cache(settings),
        checkpoint_storage_factory=CheckpointFilesystem,
        retry_policy=RetryPolicy(
            max_attempts=settings.retry_max_attempts,
            backoff_seconds=settings.retry_backoff_seconds,
        ),
        translation_concurrency=settings.chunk_translation_concurrency,
        tts_concurrency=settings.chunk_tts_concurrency,
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
    if name == "ollama":
        from app.providers.translation.ollama import OllamaTranslationProvider

        return OllamaTranslationProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_translation_model,
            timeout_seconds=settings.ollama_http_timeout_seconds,
        )
    if name == "gemini":
        api_key = (
            settings.gemini_api_key.get_secret_value().strip()
            if settings.gemini_api_key is not None
            else ""
        )
        if not api_key:
            raise ProviderConfigurationError(
                "GEMINI_API_KEY is required when TRANSLATION_PROVIDER=gemini"
            )
        from app.providers.translation.gemini import GeminiTranslationProvider

        return GeminiTranslationProvider(
            api_key=api_key,
            model=settings.gemini_translation_model,
            base_url=settings.gemini_base_url,
            timeout_seconds=settings.gemini_http_timeout_seconds,
        )
    if name == "groq":
        api_key = (
            settings.groq_api_key.get_secret_value().strip()
            if settings.groq_api_key is not None
            else ""
        )
        if not api_key:
            raise ProviderConfigurationError(
                "GROQ_API_KEY is required when TRANSLATION_PROVIDER=groq"
            )
        from app.providers.translation.openai_compatible import GroqTranslationProvider

        return GroqTranslationProvider(
            api_key=api_key,
            model=settings.groq_translation_model,
            base_url=settings.groq_base_url,
            timeout_seconds=settings.groq_http_timeout_seconds,
        )
    if name == "openrouter":
        api_key = (
            settings.openrouter_api_key.get_secret_value().strip()
            if settings.openrouter_api_key is not None
            else ""
        )
        if not api_key:
            raise ProviderConfigurationError(
                "OPENROUTER_API_KEY is required when TRANSLATION_PROVIDER=openrouter"
            )
        from app.providers.translation.openai_compatible import OpenRouterTranslationProvider

        return OpenRouterTranslationProvider(
            api_key=api_key,
            model=settings.openrouter_translation_model,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.openrouter_http_timeout_seconds,
        )
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
    return FFmpegAudioProcessor(
        encode_timeout_ceiling_seconds=float(settings.rq_job_timeout_seconds),
    )


def build_artifact_cache(settings: Settings) -> PipelineArtifactCache:
    return PipelineArtifactCache(
        FilesystemArtifactCache(settings.storage_path / "cache"),
        cache_identity_from_settings(settings),
    )


def translation_pin_from_settings(settings: Settings) -> tuple[str, str]:
    """Lab choice copied onto new stories. Not read from the create request."""
    name = settings.translation_provider.strip().lower()
    if name == "nllb":
        return name, settings.nllb_model_id
    if name == "ollama":
        return name, settings.ollama_translation_model
    if name == "gemini":
        return name, settings.gemini_translation_model
    if name == "groq":
        from app.providers.translation.openai_compatible import canonical_groq_model

        return name, canonical_groq_model(settings.groq_translation_model)
    if name == "openrouter":
        return name, settings.openrouter_translation_model
    if name == "fake":
        return "fake", "fake"
    raise UnknownProviderError(f"unknown TRANSLATION_PROVIDER: {settings.translation_provider}")


def settings_for_translation_pin(settings: Settings, job: Job) -> Settings:
    """Use the story pin when both fields are present. Otherwise keep live settings."""
    provider = job.translation_provider
    model = job.translation_model
    if provider is None or model is None:
        return settings
    if provider == "nllb":
        return settings.model_copy(update={"translation_provider": "nllb", "nllb_model_id": model})
    if provider == "ollama":
        return settings.model_copy(
            update={"translation_provider": "ollama", "ollama_translation_model": model}
        )
    if provider == "gemini":
        return settings.model_copy(
            update={"translation_provider": "gemini", "gemini_translation_model": model}
        )
    if provider == "groq":
        from app.providers.translation.openai_compatible import canonical_groq_model

        return settings.model_copy(
            update={
                "translation_provider": "groq",
                "groq_translation_model": canonical_groq_model(model),
            }
        )
    if provider == "openrouter":
        return settings.model_copy(
            update={"translation_provider": "openrouter", "openrouter_translation_model": model}
        )
    if provider == "fake":
        return settings.model_copy(update={"translation_provider": "fake"})
    raise UnknownProviderError(f"unknown translation pin: {provider}")


def cache_identity_from_settings(settings: Settings) -> CacheIdentity:
    translation = settings.translation_provider.strip().lower()
    tts = settings.tts_provider.strip().lower()
    if translation == "nllb":
        translation_model = settings.nllb_model_id
    elif translation == "ollama":
        translation_model = settings.ollama_translation_model
    elif translation == "gemini":
        translation_model = settings.gemini_translation_model
    elif translation == "groq":
        from app.providers.translation.openai_compatible import canonical_groq_model

        translation_model = canonical_groq_model(settings.groq_translation_model)
    elif translation == "openrouter":
        # Cache key is the configured id. ``openrouter/auto`` stays that literal
        # while OpenRouter may route each request to a different model, so a hit
        # can replay another model's translation. The served model is logged on
        # the response and is not part of this key.
        translation_model = settings.openrouter_translation_model
    else:
        translation_model = "fake"
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
    timeout = settings.rq_job_timeout_seconds
    queue = RQJobQueue(
        Queue(RQ_QUEUE_NAME, connection=queue_client, default_timeout=timeout),
        job_timeout=timeout,
    )
    return filesystem, store, queue


def _tts_tmp_dir(storage_path: Path) -> Path:
    return storage_path / ".provider-tmp" / "tts"
