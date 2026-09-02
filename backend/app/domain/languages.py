from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.errors import DomainError, ErrorType

if TYPE_CHECKING:
    from app.domain.ports import LanguageDetector

AUTO_SOURCE_LANGUAGE = "auto"


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
