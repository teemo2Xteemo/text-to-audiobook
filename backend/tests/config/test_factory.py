from pathlib import Path

import pytest

from app.config.factory import (
    UnknownProviderError,
    build_audio_processor,
    build_translation_provider,
    build_tts_provider,
)
from app.config.settings import Settings
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.providers.translation.fake import FakeTranslationProvider
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
    settings = Settings(_env_file=None, storage_path=tmp_path, translation_provider="nllb")
    with pytest.raises(UnknownProviderError, match="TRANSLATION_PROVIDER"):
        build_translation_provider(settings)


def test_factory_rejects_unknown_tts_provider(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, storage_path=tmp_path, tts_provider="edge")
    with pytest.raises(UnknownProviderError, match="TTS_PROVIDER"):
        build_tts_provider(settings)
