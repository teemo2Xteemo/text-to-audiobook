from collections.abc import Sequence
from typing import Protocol

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.languages import LanguageDetection


class TranslationProvider(Protocol):
    async def translate(self, text: str, source_language: str, target_language: str) -> str: ...

    def supported_languages(self) -> Sequence[str]: ...


class TTSProvider(Protocol):
    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact: ...

    def voices_for(self, language: str) -> Sequence[Voice]: ...


class NarrationProcessor(Protocol):
    def process(self, text: str, language: str) -> str: ...


class LanguageDetector(Protocol):
    async def detect(self, text: str) -> LanguageDetection: ...
