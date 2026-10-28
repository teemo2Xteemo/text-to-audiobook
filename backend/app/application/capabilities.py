from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.audio import Voice
from app.domain.ports import TranslationProvider, TTSProvider
from app.domain.translation_profile import TranslationProfile

_DEFAULT_ESTIMATE_DEFAULTS: dict[str, float] = {
    "chars_per_second": 15.0,
    "preview_wpm": 150.0,
}


@dataclass(frozen=True)
class Capabilities:
    languages: list[str]
    voices: list[Voice]
    translation_profiles: list[TranslationProfile] = field(default_factory=list)
    narrate_languages: list[str] = field(default_factory=list)
    estimate_defaults: dict[str, float] = field(default_factory=dict)


class CapabilitiesService:
    """Languages = translation ∩ TTS (at least one voice). Voices filtered by optional language."""

    def __init__(
        self,
        *,
        translation: TranslationProvider,
        tts: TTSProvider,
        translation_profiles: Sequence[TranslationProfile] | None = None,
        estimate_defaults: dict[str, float] | None = None,
    ) -> None:
        self._translation = translation
        self._tts = tts
        self._translation_profiles = (
            list(translation_profiles) if translation_profiles is not None else []
        )
        self._estimate_defaults = (
            dict(estimate_defaults)
            if estimate_defaults is not None
            else _DEFAULT_ESTIMATE_DEFAULTS.copy()
        )

    def get(self, language: str | None = None) -> Capabilities:
        languages = _intersection_languages(self._translation, self._tts)
        if language is None:
            voices = list(self._tts.all_voices())
        else:
            voices = list(self._tts.voices_for(language))
        narrate_languages = sorted(self._tts.languages())
        return Capabilities(
            languages=languages,
            voices=voices,
            translation_profiles=list(self._translation_profiles),
            narrate_languages=narrate_languages,
            estimate_defaults=dict(self._estimate_defaults),
        )


def _intersection_languages(translation: TranslationProvider, tts: TTSProvider) -> list[str]:
    result: list[str] = []
    for code in translation.supported_languages():
        if tts.voices_for(code):
            result.append(code)
    return result
