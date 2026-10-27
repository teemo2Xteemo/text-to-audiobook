from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, Protocol

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import AUTO_SOURCE_LANGUAGE

logger = logging.getLogger(__name__)

# Domain BCP-47 → Gemini prompt (code, English name). Vendor codes stay here.
_BCP47_TO_GEMINI: dict[str, tuple[str, str]] = {
    "ar-SA": ("ar", "Arabic"),
    "bn-BD": ("bn", "Bengali"),
    "cs-CZ": ("cs", "Czech"),
    "da-DK": ("da", "Danish"),
    "de-DE": ("de", "German"),
    "el-GR": ("el", "Greek"),
    "en-GB": ("en", "English"),
    "en-US": ("en", "English"),
    "es-ES": ("es", "Spanish"),
    "es-MX": ("es", "Spanish"),
    "fa-IR": ("fa", "Persian"),
    "fi-FI": ("fi", "Finnish"),
    "fr-FR": ("fr", "French"),
    "he-IL": ("he", "Hebrew"),
    "hi-IN": ("hi", "Hindi"),
    "hu-HU": ("hu", "Hungarian"),
    "id-ID": ("id", "Indonesian"),
    "it-IT": ("it", "Italian"),
    "ja-JP": ("ja", "Japanese"),
    "ko-KR": ("ko", "Korean"),
    "ms-MY": ("ms", "Malay"),
    "nb-NO": ("nb", "Norwegian Bokmål"),
    "nl-NL": ("nl", "Dutch"),
    "pl-PL": ("pl", "Polish"),
    "pt-BR": ("pt", "Portuguese"),
    "pt-PT": ("pt", "Portuguese"),
    "ro-RO": ("ro", "Romanian"),
    "ru-RU": ("ru", "Russian"),
    "sv-SE": ("sv", "Swedish"),
    "sw-KE": ("sw", "Swahili"),
    "ta-IN": ("ta", "Tamil"),
    "th-TH": ("th", "Thai"),
    "tr-TR": ("tr", "Turkish"),
    "uk-UA": ("uk", "Ukrainian"),
    "ur-PK": ("ur", "Urdu"),
    "vi-VN": ("vi", "Vietnamese"),
    "zh-CN": ("zh-Hans", "Chinese"),
    "zh-Hans": ("zh-Hans", "Chinese"),
    "zh-HK": ("zh-Hant", "Chinese"),
    "zh-Hant": ("zh-Hant", "Chinese"),
    "zh-SG": ("zh-Hans", "Chinese"),
    "zh-TW": ("zh-Hant", "Chinese"),
}

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(sorted(_BCP47_TO_GEMINI))

_MAX_INPUT_CHARS = 10000


class GeminiHttpClient(Protocol):
    async def generate_content(
        self, *, model: str, payload: dict[str, Any], api_key: str
    ) -> dict[str, Any]: ...


def bcp47_to_gemini(language: str) -> tuple[str, str]:
    mapped = _BCP47_TO_GEMINI.get(language)
    if mapped is None:
        raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "language is not supported")
    return mapped


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n(.*)\r?\n```[ \t]*$", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned


def _map_gemini_error(exc: Exception) -> DomainError:
    if isinstance(exc, DomainError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return DomainError(ErrorType.PROVIDER_RATE_LIMIT, "gemini rate limit exceeded")
        if exc.code in (400, 401, 403, 404):
            return DomainError(ErrorType.PROVIDER_CONFIGURATION_ERROR, f"gemini HTTP {exc.code}")
        if exc.code in (500, 502, 503, 504):
            return DomainError(ErrorType.TRANSLATION_FAILED, f"gemini HTTP {exc.code}")
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return DomainError(ErrorType.TIMEOUT, "gemini request timed out")
    return DomainError(ErrorType.TRANSLATION_FAILED, str(exc))


class UrllibGeminiHttpClient:
    """POST ``/v1beta/models/{model}:generateContent`` with stdlib urllib."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def generate_content(
        self, *, model: str, payload: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._generate_content_sync, model, payload, api_key)

    def _generate_content_sync(
        self, model: str, payload: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        url = f"{self._base_url}/v1beta/models/{model}:generateContent"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                content = response.read().decode("utf-8")
                return json.loads(content)
        except urllib.error.HTTPError as exc:
            raise _map_gemini_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise _map_gemini_error(exc) from exc


class GeminiTranslationProvider:
    """Google Gemini translation adapter registered as ``TRANSLATION_PROVIDER=gemini``."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "gemini-3.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com",
        timeout_seconds: float = 60.0,
        http: GeminiHttpClient | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._model = model
        self._http = (
            http
            if http is not None
            else UrllibGeminiHttpClient(
                base_url=base_url,
                timeout_seconds=timeout_seconds,
            )
        )

    def supported_languages(self) -> Sequence[str]:
        return list(_BCP47_TO_GEMINI)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        if not self._api_key:
            raise DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR,
                "GEMINI_API_KEY is not configured or empty",
            )
        if source_language == AUTO_SOURCE_LANGUAGE:
            raise DomainError(
                ErrorType.UNSUPPORTED_LANGUAGE,
                "auto is not a translation source; resolve it first",
            )
        source_code, source_name = bcp47_to_gemini(source_language)
        target_code, target_name = bcp47_to_gemini(target_language)
        if len(text) > _MAX_INPUT_CHARS:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "text exceeds model context")

        logger.info(
            "gemini_translate",
            extra={
                "provider": "gemini",
                "model": self._model,
                "source_language": source_language,
                "target_language": target_language,
                "character_count": len(text),
            },
        )

        system_prompt = (
            f"You are a professional literary translator. Translate the text from {source_name} "
            f"to {target_name}. Preserve tone, nuance, character dialogue style, and narrative "
            "flow. Return ONLY the translated text without explanations, greetings, or "
            "formatting markdown."
        )

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": text}],
                }
            ],
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "generationConfig": {
                "temperature": 0.3,
            },
        }

        try:
            data = await self._http.generate_content(
                model=self._model, payload=payload, api_key=self._api_key
            )
        except DomainError:
            raise
        except Exception as exc:
            raise _map_gemini_error(exc) from exc

        prompt_feedback = data.get("promptFeedback") or {}
        block_reason = prompt_feedback.get("blockReason")
        if block_reason:
            raise DomainError(
                ErrorType.INVALID_INPUT,
                f"gemini prompt blocked: {block_reason}",
            )

        candidates = data.get("candidates") or []
        if not candidates:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "gemini returned no candidates")

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")
        if finish_reason in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII"):
            raise DomainError(
                ErrorType.INVALID_INPUT,
                f"gemini candidate blocked by policy: {finish_reason}",
            )
        if finish_reason not in (None, "STOP"):
            raise DomainError(
                ErrorType.TRANSLATION_FAILED,
                f"gemini candidate stopped with reason: {finish_reason}",
            )

        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        if not parts:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "gemini returned empty parts")

        text_parts = [
            p["text"] for p in parts if isinstance(p, dict) and "text" in p and not p.get("thought")
        ]
        if not text_parts:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "gemini returned no text parts")

        raw_text = "".join(text_parts)
        cleaned = _strip_markdown_fences(raw_text)
        if not cleaned:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "translation output is empty")

        return cleaned
