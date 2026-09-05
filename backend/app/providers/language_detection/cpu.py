from __future__ import annotations

from langdetect import DetectorFactory, LangDetectException, detect_langs

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import LanguageDetection

# Fixed seed so sequential detect() calls are deterministic in tests.
LANGDETECT_SEED = 0

_SET_SOURCE_MESSAGE = "could not detect source language; set source_language explicitly"

# langdetect ISO / zh-cn tags → domain BCP-47. Not a "default to Chinese" fallback.
_ISO_TO_BCP47: dict[str, str] = {
    "ar": "ar-SA",
    "bn": "bn-BD",
    "cs": "cs-CZ",
    "da": "da-DK",
    "de": "de-DE",
    "el": "el-GR",
    "en": "en-US",
    "es": "es-ES",
    "et": "et-EE",
    "fa": "fa-IR",
    "fi": "fi-FI",
    "fr": "fr-FR",
    "he": "he-IL",
    "hi": "hi-IN",
    "hr": "hr-HR",
    "hu": "hu-HU",
    "id": "id-ID",
    "it": "it-IT",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "lt": "lt-LT",
    "lv": "lv-LV",
    "nl": "nl-NL",
    "no": "nb-NO",
    "pl": "pl-PL",
    "pt": "pt-BR",
    "ro": "ro-RO",
    "ru": "ru-RU",
    "sk": "sk-SK",
    "sl": "sl-SI",
    "sv": "sv-SE",
    "sw": "sw-KE",
    "ta": "ta-IN",
    "th": "th-TH",
    "tl": "fil-PH",
    "tr": "tr-TR",
    "uk": "uk-UA",
    "ur": "ur-PK",
    "vi": "vi-VN",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-tw": "zh-TW",
}


def _configure_seed() -> None:
    DetectorFactory.seed = LANGDETECT_SEED


class CpuLanguageDetector:
    """CPU detector for ``source_language=auto``. Threshold is adapter/config, not domain."""

    def __init__(self, *, min_confidence: float = 0.5) -> None:
        self._min_confidence = min_confidence
        _configure_seed()

    async def detect(self, text: str) -> LanguageDetection:
        _configure_seed()
        stripped = text.strip()
        if not stripped:
            raise DomainError(ErrorType.INVALID_INPUT, _SET_SOURCE_MESSAGE)
        try:
            candidates = detect_langs(stripped)
        except LangDetectException as exc:
            raise DomainError(ErrorType.INVALID_INPUT, _SET_SOURCE_MESSAGE) from exc
        if not candidates:
            raise DomainError(ErrorType.INVALID_INPUT, _SET_SOURCE_MESSAGE)
        top = candidates[0]
        if top.prob < self._min_confidence:
            raise DomainError(ErrorType.INVALID_INPUT, _SET_SOURCE_MESSAGE)
        mapped = _ISO_TO_BCP47.get(top.lang.strip().lower())
        if mapped is None:
            raise DomainError(ErrorType.INVALID_INPUT, _SET_SOURCE_MESSAGE)
        return LanguageDetection(language_code=mapped, confidence=float(top.prob))
