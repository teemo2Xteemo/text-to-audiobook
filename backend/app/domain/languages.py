from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.errors import DomainError, ErrorType

if TYPE_CHECKING:
    from app.domain.ports import LanguageDetector

AUTO_SOURCE_LANGUAGE = "auto"

FAMILY_PREFERENCES: dict[str, list[str]] = {
    "en": ["en-US", "en-GB", "en-AU", "en-CA", "en-IN", "en-IE", "en-NZ", "en-ZA"],
    "es": ["es-ES", "es-MX", "es-US", "es-AR", "es-CO", "es-CL"],
    "pt": ["pt-BR", "pt-PT"],
    "zh": ["zh-CN", "zh-TW", "zh-SG"],
    "zh-cn": ["zh-CN", "zh-TW", "zh-SG"],
    "zh-tw": ["zh-TW", "zh-CN", "zh-SG"],
    "fr": ["fr-FR", "fr-CA", "fr-BE", "fr-CH"],
    "de": ["de-DE", "de-AT", "de-CH"],
    "ar": ["ar-SA", "ar-EG", "ar-AE"],
    "it": ["it-IT", "it-CH"],
    "nl": ["nl-NL", "nl-BE"],
}


@dataclass(frozen=True)
class LanguageDetection:
    language_code: str
    confidence: float


def ensure_valid_languages(source_language: str, target_language: str) -> None:
    if not source_language.strip():
        raise DomainError(ErrorType.INVALID_INPUT, "source_language is required")
    if not target_language.strip():
        raise DomainError(ErrorType.INVALID_INPUT, "target_language is required")
    if target_language == AUTO_SOURCE_LANGUAGE:
        raise DomainError(ErrorType.INVALID_INPUT, "auto is not allowed for target_language")


async def resolve_source_language(
    text: str,
    source_language: str,
    detector: LanguageDetector,
) -> str:
    if source_language != AUTO_SOURCE_LANGUAGE:
        return source_language
    detection = await detector.detect(text)
    return detection.language_code
