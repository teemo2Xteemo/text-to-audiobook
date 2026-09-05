from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.audio import Voice
from app.domain.ports import TranslationProvider, TTSProvider


@dataclass(frozen=True)
class Capabilities:
    languages: list[str]
    voices: list[Voice]


class CapabilitiesService:
    """Languages = translation ∩ TTS (at least one voice). Voices filtered by optional language."""

    def __init__(self, *, translation: TranslationProvider, tts: TTSProvider) -> None:
        self._translation = translation
        self._tts = tts

    def get(self, language: str | None = None) -> Capabilities:
        languages = _intersection_languages(self._translation, self._tts)
        if language is None:
            voices = _voices_for_languages(self._tts, languages)
        else:
            voices = list(self._tts.voices_for(language))
        return Capabilities(languages=languages, voices=voices)


def _intersection_languages(translation: TranslationProvider, tts: TTSProvider) -> list[str]:
    result: list[str] = []
    for code in translation.supported_languages():
        if tts.voices_for(code):
            result.append(code)
    return result


def _voices_for_languages(tts: TTSProvider, languages: Sequence[str]) -> list[Voice]:
    voices: list[Voice] = []
    for code in languages:
        voices.extend(tts.voices_for(code))
    return voices
