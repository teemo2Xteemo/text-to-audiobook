import ast
import sys
from pathlib import Path

import pytest

from app.application.pipeline.artifact_cache import PipelineArtifactCache
from app.application.pipeline.conservative_narration import ConservativeNarrationProcessor
from app.config.factory import (
    UnknownProviderError,
    build_artifact_cache,
    build_audio_processor,
    build_language_detector,
    build_narration_processor,
    build_orchestrator,
    build_translation_provider,
    build_tts_provider,
    cache_identity_from_settings,
    settings_for_translation_pin,
    translation_pin_from_settings,
)
from app.config.settings import Settings
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.retry import RetryPolicy
from app.infrastructure.artifact_cache_fs import FilesystemArtifactCache
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.infrastructure.ffmpeg_audio import FFmpegAudioProcessor
from app.providers.language_detection.cpu import CpuLanguageDetector
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.translation.gemini import GeminiTranslationProvider
from app.providers.translation.nllb import NllbTranslationProvider
from app.providers.translation.ollama import OllamaTranslationProvider
from app.providers.tts.edge import EdgeTTSProvider
from app.providers.tts.fake import FakeTTSProvider


def test_factory_builds_fake_providers(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="fake",
        tts_provider="fake",
    )
    assert isinstance(build_translation_provider(settings), FakeTranslationProvider)
    assert isinstance(build_tts_provider(settings), FakeTTSProvider)
    assert isinstance(build_audio_processor(settings), FakeAudioProcessor)


def test_factory_rejects_unknown_translation_provider(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path, translation_provider="libre")
    with pytest.raises(UnknownProviderError, match="TRANSLATION_PROVIDER"):
        build_translation_provider(settings)


def test_factory_rejects_unknown_tts_provider(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path, tts_provider="piper")
    with pytest.raises(UnknownProviderError, match="TTS_PROVIDER"):
        build_tts_provider(settings)


def test_factory_builds_edge_provider_without_calling_sdk(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        tts_provider="edge",
        tts_default_voice_by_language="ja-JP=ja-JP-AdapterANeural",
    )
    provider = build_tts_provider(settings)
    assert isinstance(provider, EdgeTTSProvider)
    assert provider._defaults == {"ja-JP": "ja-JP-AdapterANeural"}
    processor = build_audio_processor(settings)
    assert isinstance(processor, FFmpegAudioProcessor)
    assert processor._encode_timeout_ceiling_seconds == 1800.0


def test_ffmpeg_processor_ceiling_follows_job_timeout(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        tts_provider="edge",
        rq_job_timeout_seconds=3600,
    )
    processor = build_audio_processor(settings)
    assert isinstance(processor, FFmpegAudioProcessor)
    assert processor._encode_timeout_ceiling_seconds == 3600.0


def test_factory_builds_conservative_narration() -> None:
    assert isinstance(build_narration_processor(), ConservativeNarrationProcessor)


def test_artifact_cache_root_is_storage_cache_without_cache_path_setting(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path)
    cache = build_artifact_cache(settings)
    assert isinstance(cache, PipelineArtifactCache)
    assert isinstance(cache._store, FilesystemArtifactCache)
    assert cache._store._root == tmp_path / "cache"
    assert "cache_path" not in Settings.model_fields


def test_build_orchestrator_injects_conservative_narration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class Capture:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

    monkeypatch.setattr("app.config.factory.PipelineOrchestrator", Capture)
    monkeypatch.setattr(
        "app.config.factory._infrastructure",
        lambda settings: (object(), object(), object()),
    )
    monkeypatch.setattr(
        "app.config.factory.build_translation_provider",
        lambda settings: FakeTranslationProvider(),
    )
    monkeypatch.setattr(
        "app.config.factory.build_tts_provider",
        lambda settings: FakeTTSProvider(output_dir=tmp_path),
    )
    monkeypatch.setattr(
        "app.config.factory.build_audio_processor",
        lambda settings: FakeAudioProcessor(),
    )
    settings = Settings(_env_file=None, storage_path=tmp_path)
    build_orchestrator(settings)
    assert isinstance(seen["narration"], ConservativeNarrationProcessor)
    assert isinstance(seen["artifact_cache"], PipelineArtifactCache)
    assert isinstance(seen["detector"], CpuLanguageDetector)
    assert seen["retry_policy"] == RetryPolicy(max_attempts=3, backoff_seconds=1.0)
    assert seen["translation_concurrency"] == 2
    assert seen["tts_concurrency"] == 2


def test_build_orchestrator_injects_retry_policy_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class Capture:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

    monkeypatch.setattr("app.config.factory.PipelineOrchestrator", Capture)
    monkeypatch.setattr(
        "app.config.factory._infrastructure",
        lambda settings: (object(), object(), object()),
    )
    monkeypatch.setattr(
        "app.config.factory.build_translation_provider",
        lambda settings: FakeTranslationProvider(),
    )
    monkeypatch.setattr(
        "app.config.factory.build_tts_provider",
        lambda settings: FakeTTSProvider(output_dir=tmp_path),
    )
    monkeypatch.setattr(
        "app.config.factory.build_audio_processor",
        lambda settings: FakeAudioProcessor(),
    )
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "2.0")
    settings = Settings(_env_file=None, storage_path=tmp_path)
    assert settings.retry_max_attempts == 5
    assert settings.retry_backoff_seconds == 2.0
    build_orchestrator(settings)
    assert seen["retry_policy"] == RetryPolicy(max_attempts=5, backoff_seconds=2.0)


def test_build_orchestrator_injects_concurrency_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    class Capture:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

    monkeypatch.setattr("app.config.factory.PipelineOrchestrator", Capture)
    monkeypatch.setattr(
        "app.config.factory._infrastructure",
        lambda settings: (object(), object(), object()),
    )
    monkeypatch.setattr(
        "app.config.factory.build_translation_provider",
        lambda settings: FakeTranslationProvider(),
    )
    monkeypatch.setattr(
        "app.config.factory.build_tts_provider",
        lambda settings: FakeTTSProvider(output_dir=tmp_path),
    )
    monkeypatch.setattr(
        "app.config.factory.build_audio_processor",
        lambda settings: FakeAudioProcessor(),
    )
    monkeypatch.setenv("CHUNK_TRANSLATION_CONCURRENCY", "4")
    monkeypatch.setenv("CHUNK_TTS_CONCURRENCY", "3")
    settings = Settings(_env_file=None, storage_path=tmp_path)
    build_orchestrator(settings)
    assert seen["translation_concurrency"] == 4
    assert seen["tts_concurrency"] == 3


def test_factory_builds_nllb_provider_without_loading_weights(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="nllb",
        nllb_model_id="facebook/nllb-200-distilled-600M",
    )
    provider = build_translation_provider(settings)
    assert isinstance(provider, NllbTranslationProvider)
    languages = set(provider.supported_languages())
    assert "zh-CN" in languages
    assert "vi-VN" in languages
    assert "ja-JP" in languages
    assert "en-US" in languages


def test_factory_builds_ollama_provider_without_network(tmp_path: Path) -> None:
    sys.modules.pop("app.providers.translation.nllb", None)
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="ollama",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_translation_model="translategemma:4b",
        ollama_http_timeout_seconds=120.0,
    )
    provider = build_translation_provider(settings)
    assert isinstance(provider, OllamaTranslationProvider)
    languages = set(provider.supported_languages())
    assert "zh-CN" in languages
    assert "vi-VN" in languages
    assert "ja-JP" in languages
    assert "en-US" in languages
    assert "app.providers.translation.nllb" not in sys.modules


def test_cache_identity_from_settings_uses_provider_model(tmp_path: Path) -> None:
    nllb = cache_identity_from_settings(
        Settings(
            _env_file=None,
            storage_path=tmp_path,
            translation_provider="nllb",
            nllb_model_id="facebook/nllb-200-distilled-1.3B",
        )
    )
    assert nllb.translation_provider == "nllb"
    assert nllb.translation_model == "facebook/nllb-200-distilled-1.3B"

    ollama = cache_identity_from_settings(
        Settings(
            _env_file=None,
            storage_path=tmp_path,
            translation_provider="ollama",
            ollama_translation_model="translategemma:12b",
        )
    )
    assert ollama.translation_provider == "ollama"
    assert ollama.translation_model == "translategemma:12b"

    fake = cache_identity_from_settings(
        Settings(_env_file=None, storage_path=tmp_path, translation_provider="fake")
    )
    assert fake.translation_provider == "fake"
    assert fake.translation_model == "fake"


def test_translation_pins_do_not_construct_the_other_heavy_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.providers.translation.nllb as nllb_mod
    import app.providers.translation.ollama as ollama_mod

    settings = Settings(_env_file=None, storage_path=tmp_path)
    assert settings.translation_provider == "fake"
    constructed: list[str] = []
    nllb_init = nllb_mod.NllbTranslationProvider.__init__
    ollama_init = ollama_mod.OllamaTranslationProvider.__init__

    def _spy_nllb(self: object, *args: object, **kwargs: object) -> None:
        constructed.append("nllb")
        nllb_init(self, *args, **kwargs)

    def _spy_ollama(self: object, *args: object, **kwargs: object) -> None:
        constructed.append("ollama")
        ollama_init(self, *args, **kwargs)

    monkeypatch.setattr(nllb_mod.NllbTranslationProvider, "__init__", _spy_nllb)
    monkeypatch.setattr(ollama_mod.OllamaTranslationProvider, "__init__", _spy_ollama)

    fake = build_translation_provider(settings)
    assert isinstance(fake, FakeTranslationProvider)
    assert constructed == []

    nllb = build_translation_provider(
        settings.model_copy(
            update={
                "translation_provider": "nllb",
                "nllb_model_id": "facebook/nllb-200-distilled-600M",
            }
        )
    )
    assert type(nllb) is nllb_mod.NllbTranslationProvider
    assert constructed == ["nllb"]

    ollama = build_translation_provider(
        settings.model_copy(
            update={
                "translation_provider": "ollama",
                "ollama_translation_model": "translategemma:4b",
            }
        )
    )
    assert type(ollama) is ollama_mod.OllamaTranslationProvider
    assert constructed == ["nllb", "ollama"]


def test_cache_identity_follows_the_job_pin_not_live_settings(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="nllb",
        nllb_model_id="facebook/nllb-200-distilled-600M",
    )
    job = Job(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="ja-JP",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
        translation_provider="ollama",
        translation_model="translategemma:12b",
    )
    identity = cache_identity_from_settings(settings_for_translation_pin(settings, job))
    assert identity.translation_provider == "ollama"
    assert identity.translation_model == "translategemma:12b"
    live = cache_identity_from_settings(settings)
    assert live.translation_model == "facebook/nllb-200-distilled-600M"


def test_factory_builds_cpu_language_detector(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path, language_detect_min_confidence=0.5)
    assert isinstance(build_language_detector(settings), CpuLanguageDetector)


def test_build_job_service_uses_dedicated_max_recovery_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config.factory import build_job_service

    monkeypatch.setattr(
        "app.config.factory._infrastructure",
        lambda settings: (object(), object(), object()),
    )
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        retry_max_attempts=5,
        max_recovery_attempts=2,
    )
    service = build_job_service(settings)
    assert service._max_recovery_attempts == 2


def test_factory_module_has_no_top_level_vendor_imports() -> None:
    path = Path(__file__).resolve().parents[2] / "app" / "config" / "factory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"torch", "transformers", "edge_tts", "langdetect"})


def test_factory_builds_gemini_provider(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="gemini",
        gemini_api_key="test-key",
        gemini_translation_model="gemini-3.5-flash",
    )
    provider = build_translation_provider(settings)
    assert isinstance(provider, GeminiTranslationProvider)
    assert provider._model == "gemini-3.5-flash"
    assert provider._api_key == "test-key"


def test_factory_gemini_without_api_key_raises_value_error(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="gemini",
        gemini_api_key=None,
    )
    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        build_translation_provider(settings)

    empty_settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="gemini",
        gemini_api_key="   ",
    )
    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        build_translation_provider(empty_settings)


def test_gemini_translation_pin_and_cache_identity(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="gemini",
        gemini_api_key="test-key",
        gemini_translation_model="gemini-3.5-flash",
    )
    pin_name, pin_model = translation_pin_from_settings(settings)
    assert pin_name == "gemini"
    assert pin_model == "gemini-3.5-flash"
    cache_id = cache_identity_from_settings(settings)
    assert cache_id.translation_provider == "gemini"
    assert cache_id.translation_model == "gemini-3.5-flash"


def test_gemini_settings_for_translation_pin(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="fake",
    )
    job = Job(
        id="11111111-2222-3333-4444-555555555555",
        status=JobStatus.QUEUED,
        source_language="zh-CN",
        target_language="vi-VN",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
        translation_provider="gemini",
        translation_model="gemini-1.5-flash",
    )
    pinned = settings_for_translation_pin(settings, job)
    assert pinned.translation_provider == "gemini"
    assert pinned.gemini_translation_model == "gemini-1.5-flash"


def test_build_available_translation_profiles_fake_mode() -> None:
    from app.config.factory import build_available_translation_profiles

    settings = Settings(_env_file=None, translation_provider="fake")
    profiles = build_available_translation_profiles(settings)
    assert len(profiles) == 1
    assert profiles[0].id == "fake"
    assert profiles[0].provider == "fake"
    assert profiles[0].is_default is True


def test_build_available_translation_profiles_with_gemini_and_nllb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config.factory import build_available_translation_profiles

    monkeypatch.setattr("app.config.factory._nllb_available", lambda: True)
    settings = Settings(
        _env_file=None,
        translation_provider="nllb",
        gemini_api_key="valid-key",
        gemini_translation_model="gemini-3.5-flash",
        nllb_model_id="facebook/nllb-200-distilled-600M",
    )
    profiles = build_available_translation_profiles(settings)
    ids = [p.id for p in profiles]
    assert "gemini-3.5-flash" in ids
    assert "gemini-2.0-flash" in ids
    assert "nllb-600m" in ids
    nllb_profile = next(p for p in profiles if p.id == "nllb-600m")
    assert nllb_profile.is_default is True


def test_build_available_translation_profiles_omits_gemini_when_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config.factory import build_available_translation_profiles

    monkeypatch.setattr("app.config.factory._nllb_available", lambda: True)
    settings = Settings(_env_file=None, translation_provider="nllb", gemini_api_key=None)
    profiles = build_available_translation_profiles(settings)
    ids = [p.id for p in profiles]
    assert not any("gemini" in pid for pid in ids)
    assert "nllb-600m" in ids


def test_build_available_translation_profiles_with_ollama() -> None:
    from app.config.factory import build_available_translation_profiles

    settings = Settings(
        _env_file=None,
        translation_provider="ollama",
        ollama_translation_model="translategemma:4b",
    )
    profiles = build_available_translation_profiles(settings)
    ids = [p.id for p in profiles]
    assert "ollama-gemma" in ids
    ollama_profile = next(p for p in profiles if p.id == "ollama-gemma")
    assert ollama_profile.is_default is True


def test_build_available_translation_profiles_configured_custom_model_becomes_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config.factory import build_available_translation_profiles

    # Custom gemini model
    settings = Settings(
        _env_file=None,
        translation_provider="gemini",
        gemini_api_key="valid-key",
        gemini_translation_model="gemini-1.5-flash",
    )
    profiles = build_available_translation_profiles(settings)
    default_p = next(p for p in profiles if p.is_default)
    assert default_p.provider == "gemini"
    assert default_p.model == "gemini-1.5-flash"

    # Custom nllb model
    monkeypatch.setattr("app.config.factory._nllb_available", lambda: True)
    settings_nllb = Settings(
        _env_file=None,
        translation_provider="nllb",
        nllb_model_id="facebook/nllb-200-3.3B",
    )
    profiles_nllb = build_available_translation_profiles(settings_nllb)
    default_nllb = next(p for p in profiles_nllb if p.is_default)
    assert default_nllb.provider == "nllb"
    assert default_nllb.model == "facebook/nllb-200-3.3B"


def test_build_available_translation_profiles_omits_nllb_when_packages_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config.factory import build_available_translation_profiles

    settings = Settings(
        _env_file=None,
        translation_provider="gemini",
        gemini_api_key="valid-key",
    )
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    profiles = build_available_translation_profiles(settings)
    ids = [p.id for p in profiles]
    assert not any("nllb" in pid for pid in ids)


def test_build_translation_provider_groq_missing_and_present_key() -> None:
    from app.config.factory import ProviderConfigurationError
    from app.providers.translation.openai_compatible import GroqTranslationProvider

    settings_no_key = Settings(_env_file=None, translation_provider="groq", groq_api_key=None)
    with pytest.raises(ProviderConfigurationError, match="GROQ_API_KEY is required"):
        build_translation_provider(settings_no_key)

    settings_with_key = Settings(
        _env_file=None, translation_provider="groq", groq_api_key="gsk_valid_key"
    )
    provider = build_translation_provider(settings_with_key)
    assert isinstance(provider, GroqTranslationProvider)


def test_build_translation_provider_openrouter_missing_and_present_key() -> None:
    from app.config.factory import ProviderConfigurationError
    from app.providers.translation.openai_compatible import OpenRouterTranslationProvider

    settings_no_key = Settings(
        _env_file=None, translation_provider="openrouter", openrouter_api_key=None
    )
    with pytest.raises(ProviderConfigurationError, match="OPENROUTER_API_KEY is required"):
        build_translation_provider(settings_no_key)

    settings_with_key = Settings(
        _env_file=None, translation_provider="openrouter", openrouter_api_key="sk-or-valid_key"
    )
    provider = build_translation_provider(settings_with_key)
    assert isinstance(provider, OpenRouterTranslationProvider)


def test_translation_pin_from_settings_groq_and_openrouter() -> None:
    settings_groq = Settings(
        _env_file=None,
        translation_provider="groq",
        groq_translation_model="qwen/qwen3.8-27b",
    )
    assert translation_pin_from_settings(settings_groq) == ("groq", "qwen/qwen3.8-27b")

    settings_or = Settings(
        _env_file=None,
        translation_provider="openrouter",
        openrouter_translation_model="openrouter/auto",
    )
    assert translation_pin_from_settings(settings_or) == (
        "openrouter",
        "openrouter/auto",
    )


def test_build_available_translation_profiles_with_groq_and_openrouter() -> None:
    from app.config.factory import build_available_translation_profiles

    settings = Settings(
        _env_file=None,
        translation_provider="groq",
        groq_api_key="gsk_key",
        openrouter_api_key="sk-or-key",
    )
    profiles = build_available_translation_profiles(settings)
    ids = [p.id for p in profiles]
    assert "groq-qwen" in ids
    assert "groq-llama-70b" in ids
    assert "groq-llama-8b" in ids
    assert "openrouter-auto" in ids

    # Default profile matches configured translation_provider
    default_p = next(p for p in profiles if p.is_default)
    assert default_p.provider == "groq"
    assert default_p.model == "qwen/qwen3.8-27b"
