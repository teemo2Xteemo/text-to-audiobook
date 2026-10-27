from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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
# Completion budget for one Groq call. Pipeline chunks are at most 1200
# characters, so 8192 tokens covers a full translation plus low-effort
# gpt-oss reasoning (several tokens per source character). Inputs above
# _MAX_INPUT_CHARS are rejected before the request. A completion that still
# hits this cap is refused via finish_reason, not cached.
_GROQ_MAX_COMPLETION_TOKENS = 8192
GROQ_QWEN_MODEL = "qwen/qwen3.8-27b"
GROQ_GPT_OSS_120B = "openai/gpt-oss-120b"
GROQ_GPT_OSS_20B = "openai/gpt-oss-20b"
# Free/developer tiers shut these down on 2026-08-16. Saved job pins and
# GROQ_TRANSLATION_MODEL may still name them.
_RETIRED_GROQ_MODELS = {
    "llama-3.3-70b-versatile": GROQ_GPT_OSS_120B,
    "llama-3.1-8b-instant": GROQ_GPT_OSS_20B,
}
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN = re.compile(r"<think>.*\Z", re.IGNORECASE | re.DOTALL)
_ERROR_BODY_LIMIT = 2048
_DETAIL_LIMIT = 200
_CLIENT_MESSAGE_LIMIT = 300
_MAX_RETRY_AFTER_SECONDS = 60.0
_OPENROUTER_HTTP_REFERER = "https://github.com/teemo2Xteemo/text-to-audiobook"
_SECRET_RE = re.compile(
    r"(?i)\b(?:bearer\s+)[a-z0-9._\-]{8,}\b|(?:gsk_|sk-or-|sk-)[a-z0-9._\-]{8,}"
)


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


def canonical_groq_model(model: str) -> str:
    """Map a retired Groq model id onto the current replacement."""
    return _RETIRED_GROQ_MODELS.get(model.strip(), model.strip())


def groq_completion_options(model: str) -> dict[str, Any]:
    """Groq chat fields that keep reasoning out of the spoken translation.

    gpt-oss always reasons. ``reasoning_effort`` is only ``low``/``medium``/
    ``high``. ``include_reasoning`` false omits the trace. Do not also send
    ``reasoning_format``. Qwen3 preview puts ``<think>`` in ``content`` unless
    ``reasoning_effort`` is ``none``.
    """
    if model.startswith("openai/gpt-oss"):
        return {
            "reasoning_effort": "low",
            "include_reasoning": False,
            "max_completion_tokens": _GROQ_MAX_COMPLETION_TOKENS,
        }
    # Only the pinned preview id. A future Qwen3 id may not accept "none".
    if model == GROQ_QWEN_MODEL:
        return {
            "reasoning_effort": "none",
            "max_completion_tokens": _GROQ_MAX_COMPLETION_TOKENS,
        }
    return {}


def _reject_incomplete_choice(choice: dict[str, Any], *, model: str) -> None:
    """Refuse a completion that is not a finished answer.

    A missing finish_reason is left alone. ``stop`` is success. Anything else,
    including ``length`` and ``content_filter``, is not retryable: the same
    request and budget would fail the same way, and a partial ``content`` must
    not be cached or spoken.
    """
    if "finish_reason" not in choice:
        return
    reason = choice.get("finish_reason")
    if reason is None:
        return
    if isinstance(reason, str) and reason.strip().lower() in {"", "stop"}:
        return
    shown = reason.strip().lower() if isinstance(reason, str) else str(reason)
    raise DomainError(
        ErrorType.INVALID_INPUT,
        f"translation incomplete: model {model} finish_reason {shown}",
    )


def _visible_translation(content: str) -> str:
    """Drop reasoning markup. Never reads a separate reasoning field."""
    without_blocks = _THINK_BLOCK.sub("", content)
    return _THINK_OPEN.sub("", without_blocks).strip()


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n(.*)\r?\n```[ \t]*$", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()
    return cleaned


def _sanitize_provider_detail(text: str) -> str:
    cleaned = _SECRET_RE.sub("[redacted]", text)
    cleaned = " ".join(cleaned.split())
    return cleaned[:_DETAIL_LIMIT]


def _json_error_message(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    if isinstance(error, str):
        return error
    message = payload.get("message")
    if isinstance(message, str):
        return message
    return ""


def _read_error_body(exc: urllib.error.HTTPError) -> bytes:
    try:
        raw = exc.read(_ERROR_BODY_LIMIT)
    except Exception:
        return b""
    if isinstance(raw, bytes):
        return raw
    return b""


def _provider_detail(exc: urllib.error.HTTPError) -> str:
    raw = _read_error_body(exc)
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text or text.lstrip().startswith("<"):
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _sanitize_provider_detail(text)
    message = _json_error_message(payload)
    if message:
        return _sanitize_provider_detail(message)
    return _sanitize_provider_detail(text)


def _parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        seconds = _retry_after_http_date(text, now=now)
        if seconds is None:
            return None
    if seconds < 0:
        return 0.0
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _retry_after_http_date(value: str, *, now: datetime | None) -> float | None:
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    clock = now or datetime.now(UTC)
    return (when - clock).total_seconds()


def _retry_after_header(exc: urllib.error.HTTPError) -> str | None:
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    return str(value)


def _http_client_message(code: int, detail: str, *, model: str | None) -> str:
    head = f"HTTP {code}"
    if code == 404 and model:
        head = f"{head}: model {model}"
    if detail:
        head = f"{head}: {detail}"
    return head[:_CLIENT_MESSAGE_LIMIT]


def _is_timeout_failure(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)


def _payload_model(payload: dict[str, Any]) -> str | None:
    model = payload.get("model")
    if isinstance(model, str) and model:
        return model
    return None


def _map_openai_error(
    exc: Exception,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> DomainError:
    if isinstance(exc, DomainError):
        return exc
    mapped: DomainError
    if isinstance(exc, urllib.error.HTTPError):
        detail = _provider_detail(exc)
        retry_after = (
            _parse_retry_after(_retry_after_header(exc)) if exc.code in (429, 503) else None
        )
        if exc.code == 429:
            message = _http_client_message(exc.code, detail or "rate limit exceeded", model=None)
            mapped = DomainError(
                ErrorType.PROVIDER_RATE_LIMIT,
                message,
                retry_after_seconds=retry_after,
            )
        elif exc.code in (401, 403, 404):
            mapped = DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR,
                _http_client_message(exc.code, detail, model=model if exc.code == 404 else None),
            )
        else:
            mapped = DomainError(
                ErrorType.TRANSLATION_FAILED,
                _http_client_message(exc.code, detail, model=None),
                retry_after_seconds=retry_after,
            )
    elif _is_timeout_failure(exc):
        mapped = DomainError(ErrorType.TIMEOUT, "request timed out")
    elif isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        reason_text = str(reason) if reason is not None else str(exc)
        detail = _sanitize_provider_detail(reason_text)
        message = "connection failed" if not detail else f"connection failed: {detail}"
        mapped = DomainError(ErrorType.TRANSLATION_FAILED, message[:_CLIENT_MESSAGE_LIMIT])
    else:
        mapped = DomainError(
            ErrorType.TRANSLATION_FAILED,
            _sanitize_provider_detail(str(exc)) or "translation failed",
        )
    logger.warning(
        "openai_compatible_request_failed",
        extra={
            "provider": provider,
            "model": model,
            "error_type": mapped.error_type.value,
            "detail": mapped.message,
            "retry_after_seconds": mapped.retry_after_seconds,
        },
    )
    return mapped


class UrllibOpenAIHttpClient:
    """POST ``{base_url}/chat/completions`` using standard library urllib."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        provider_name: str = "openai-compatible",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._provider_name = provider_name

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
        model = _payload_model(payload)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                content = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                mapped = _map_openai_error(exc, model=model, provider=self._provider_name)
            finally:
                exc.close()
            raise mapped from exc
        except urllib.error.URLError as exc:
            raise _map_openai_error(exc, model=model, provider=self._provider_name) from exc
        except TimeoutError as exc:
            raise _map_openai_error(exc, model=model, provider=self._provider_name) from exc
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise DomainError(
                ErrorType.TRANSLATION_FAILED,
                "provider returned a non-JSON response",
            ) from exc
        if not isinstance(parsed, dict):
            raise DomainError(
                ErrorType.TRANSLATION_FAILED,
                "provider returned a non-JSON response",
            )
        return parsed


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
        provider_name: str = "openai-compatible",
    ) -> None:
        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._provider_name = provider_name
        self._extra_headers = extra_headers or {}
        self._http = (
            http
            if http is not None
            else UrllibOpenAIHttpClient(
                base_url=self._base_url,
                timeout_seconds=timeout_seconds,
                provider_name=provider_name,
            )
        )

    def supported_languages(self) -> Sequence[str]:
        return list(_BCP47_TO_OPENAI)

    def _completion_options(self) -> dict[str, Any]:
        return {}

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
            raise DomainError(ErrorType.INVALID_INPUT, "text exceeds model context")

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
        payload.update(self._completion_options())

        try:
            data = await self._http.create_chat_completion(
                payload=payload,
                api_key=self._api_key,
                extra_headers=self._extra_headers,
            )
        except DomainError:
            raise
        except Exception as exc:
            raise _map_openai_error(exc, model=self._model, provider=self._provider_name) from exc

        choices = data.get("choices")
        if not choices or not isinstance(choices, list):
            raise DomainError(ErrorType.TRANSLATION_FAILED, "empty or invalid choices in response")

        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        _reject_incomplete_choice(first_choice, model=self._model)

        served_model = data.get("model") if isinstance(data, dict) else None
        logger.info(
            "openai_compatible_translated",
            extra={
                "provider": self._provider_name,
                "model": self._model,
                "served_model": served_model if isinstance(served_model, str) else None,
                "base_url": self._base_url,
                "source_language": source_language,
                "target_language": target_language,
            },
        )

        message = first_choice.get("message") or {}
        if not isinstance(message, dict):
            message = {}
        translated = message.get("content", "")
        if not isinstance(translated, str):
            raise DomainError(ErrorType.TRANSLATION_FAILED, "empty translation content received")

        cleaned = _strip_markdown_fences(_visible_translation(translated))
        if not cleaned:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "translation output is empty")
        return cleaned


class GroqTranslationProvider(OpenAICompatibleTranslationProvider):
    """Groq translation adapter registered as ``TRANSLATION_PROVIDER=groq``."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = GROQ_QWEN_MODEL,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 60.0,
        http: OpenAICompatibleHttpClient | None = None,
    ) -> None:
        resolved = canonical_groq_model(model)
        if resolved != model.strip():
            logger.warning(
                "groq_retired_model_rewritten",
                extra={"model": model, "replacement": resolved},
            )
        super().__init__(
            api_key=api_key,
            model=resolved,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            extra_headers=None,
            http=http,
            provider_name="groq",
        )

    def _completion_options(self) -> dict[str, Any]:
        return groq_completion_options(self._model)


class OpenRouterTranslationProvider(OpenAICompatibleTranslationProvider):
    """OpenRouter adapter registered as ``TRANSLATION_PROVIDER=openrouter``.

    ``openrouter/auto`` is cached under that literal model id. OpenRouter may
    route each request to a different model, so a cache hit can replay another
    model's output. The answering model is logged as ``served_model`` and is
    not part of the cache key. ``openrouter/auto`` can also select a paid model.
    """

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
            "HTTP-Referer": _OPENROUTER_HTTP_REFERER,
            "X-Title": "Text to Audiobook Story Narrator",
        }
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            extra_headers=extra_headers,
            http=http,
            provider_name="openrouter",
        )
