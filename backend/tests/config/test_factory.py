import ast
from pathlib import Path

import pytest

from app.application.pipeline.conservative_narration import ConservativeNarrationProcessor
from app.config.factory import (
    UnknownProviderError,
    build_audio_processor,
    build_language_detector,
    build_narration_processor,
    build_orchestrator,
    build_translation_provider,
    build_tts_provider,
)
from app.config.settings import Settings
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.providers.language_detection.cpu import CpuLanguageDetector
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.translation.nllb import NllbTranslationProvider
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
    settings = Settings(_env_file=None, storage_path=tmp_path, tts_provider="edge")
    with pytest.raises(UnknownProviderError, match="TTS_PROVIDER"):
        build_tts_provider(settings)


def test_factory_builds_conservative_narration() -> None:
    assert isinstance(build_narration_processor(), ConservativeNarrationProcessor)


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
    assert isinstance(seen["detector"], CpuLanguageDetector)


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


def test_factory_builds_cpu_language_detector(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path, language_detect_min_confidence=0.5)
    assert isinstance(build_language_detector(settings), CpuLanguageDetector)


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
