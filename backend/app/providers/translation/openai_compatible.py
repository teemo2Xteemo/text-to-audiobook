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

# Domain BCP-47 → prompt (code, English name).
_BCP47_TO_OPENAI: dict[str, tuple[str, str]] = {
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

SUPPORTED_LANGUAGES: tuple[str, ...] = tuple(sorted(_BCP47_TO_OPENAI))

_MAX_INPUT_CHARS = 10000


class OpenAICompatibleHttpClient(Protocol):
    async def create_chat_completion(
        self,
        *,
        payload: dict[str, Any],
        api_key: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


def bcp47_to_openai_lang(language: str) -> tuple[str, str]:
    mapped = _BCP47_TO_OPENAI.get(language)
    if mapped is None:
        raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "language is not supported")
    return mapped


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n(.*)\r?\n```[ \t]*$", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned


def _map_openai_error(exc: Exception) -> DomainError:
    if isinstance(exc, DomainError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            return DomainError(ErrorType.PROVIDER_RATE_LIMIT, "rate limit exceeded")
        if exc.code in (400, 401, 403, 404):
            return DomainError(ErrorType.PROVIDER_CONFIGURATION_ERROR, f"HTTP {exc.code}")
        if exc.code in (500, 502, 503, 504):
            return DomainError(ErrorType.TRANSLATION_FAILED, f"HTTP {exc.code}")
        return DomainError(ErrorType.TRANSLATION_FAILED, f"HTTP {exc.code}")
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return DomainError(ErrorType.TIMEOUT, "request timed out")
    return DomainError(ErrorType.TRANSLATION_FAILED, str(exc))


class UrllibOpenAIHttpClient:
    """POST ``{base_url}/chat/completions`` using standard library urllib."""

    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def create_chat_completion(
        self,
        *,
        payload: dict[str, Any],
        api_key: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._create_chat_completion_sync, payload, api_key, extra_headers
        )

    def _create_chat_completion_sync(
        self,
        payload: dict[str, Any],
        api_key: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "TextToAudiobook/1.0",
        }
        if extra_headers:
            headers.update(extra_headers)

        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                content = response.read().decode("utf-8")
                return json.loads(content)
        except urllib.error.HTTPError as exc:
            raise _map_openai_error(exc) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise _map_openai_error(exc) from exc


class OpenAICompatibleTranslationProvider:
    """Base OpenAI-compatible translation adapter for Groq, OpenRouter, and standard endpoints."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str,
        timeout_seconds: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        http: OpenAICompatibleHttpClient | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._extra_headers = extra_headers or {}
        self._http = (
            http
            if http is not None
            else UrllibOpenAIHttpClient(
                base_url=self._base_url,
                timeout_seconds=timeout_seconds,
            )
        )

    def supported_languages(self) -> Sequence[str]:
        return list(_BCP47_TO_OPENAI)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        if not self._api_key:
            raise DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR,
                "API key is not configured or empty",
            )
        if source_language == AUTO_SOURCE_LANGUAGE:
            raise DomainError(
                ErrorType.UNSUPPORTED_LANGUAGE,
                "auto is not a translation source; resolve it first",
            )
        source_code, source_name = bcp47_to_openai_lang(source_language)
        target_code, target_name = bcp47_to_openai_lang(target_language)
        if len(text) > _MAX_INPUT_CHARS:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "text exceeds model context")

        logger.info(
            "openai_compatible_translate",
            extra={
                "model": self._model,
                "base_url": self._base_url,
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
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "temperature": 0.3,
        }

        try:
            data = await self._http.create_chat_completion(
                payload=payload,
                api_key=self._api_key,
                extra_headers=self._extra_headers,
            )
        except DomainError:
            raise
        except Exception as exc:
            raise _map_openai_error(exc) from exc

        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            raise DomainError(ErrorType.TRANSLATION_FAILED, "empty or invalid choices in response")

        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        translated = message.get("content", "")
        if not isinstance(translated, str):
            raise DomainError(ErrorType.TRANSLATION_FAILED, "empty translation content received")

        cleaned = _strip_markdown_fences(translated)
        if not cleaned:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "translation output is empty")
        return cleaned


class GroqTranslationProvider(OpenAICompatibleTranslationProvider):
    """Groq translation adapter registered as ``TRANSLATION_PROVIDER=groq``."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "qwen/qwen3.8-27b",
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 60.0,
        http: OpenAICompatibleHttpClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            extra_headers=None,
            http=http,
        )


class OpenRouterTranslationProvider(OpenAICompatibleTranslationProvider):
    """OpenRouter translation adapter registered as ``TRANSLATION_PROVIDER=openrouter``."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "openrouter/auto",
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_seconds: float = 60.0,
        http: OpenAICompatibleHttpClient | None = None,
    ) -> None:
        extra_headers = {
            "HTTP-Referer": "https://github.com/teemo/text-to-audiobook",
            "X-Title": "Text to Audiobook Story Narrator",
        }
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            extra_headers=extra_headers,
            http=http,
        )
