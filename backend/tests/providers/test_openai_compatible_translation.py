from __future__ import annotations

import urllib.error
from email.message import Message
from io import BytesIO
from typing import Any

import pytest

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import AUTO_SOURCE_LANGUAGE
from app.providers.translation.openai_compatible import (
    GroqTranslationProvider,
    OpenAICompatibleTranslationProvider,
    OpenRouterTranslationProvider,
    bcp47_to_openai_lang,
)


class FakeOpenAIHttp:
    def __init__(self, response_data: dict[str, Any] | None = None) -> None:
        self.response_data = (
            response_data
            if response_data is not None
            else {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Bản dịch truyện mẫu từ Qwen.",
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        self.calls: list[dict[str, Any]] = []
        self.fail: BaseException | None = None

    async def create_chat_completion(
        self,
        *,
        payload: dict[str, Any],
        api_key: str,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "payload": payload,
                "api_key": api_key,
                "extra_headers": extra_headers,
            }
        )
        if self.fail is not None:
            raise self.fail
        return self.response_data


def _provider(
    http: FakeOpenAIHttp | None = None,
    api_key: str | None = "gsk_test_groq_key",
    model: str = "qwen/qwen3.8-27b",
    base_url: str = "https://api.groq.com/openai/v1",
) -> OpenAICompatibleTranslationProvider:
    return OpenAICompatibleTranslationProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=60.0,
        http=http if http is not None else FakeOpenAIHttp(),
    )


def test_bcp47_mapping_valid_and_unsupported() -> None:
    code, name = bcp47_to_openai_lang("zh-CN")
    assert code == "zh-Hans"
    assert name == "Chinese"

    code_vi, name_vi = bcp47_to_openai_lang("vi-VN")
    assert code_vi == "vi"
    assert name_vi == "Vietnamese"

    with pytest.raises(DomainError) as exc_info:
        bcp47_to_openai_lang("unsupported-lang")
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE


def test_supported_languages_list() -> None:
    provider = _provider()
    langs = provider.supported_languages()
    assert "zh-CN" in langs
    assert "vi-VN" in langs
    assert "en-US" in langs
    assert "ja-JP" in langs


@pytest.mark.anyio
async def test_successful_translation() -> None:
    fake_http = FakeOpenAIHttp()
    provider = _provider(http=fake_http)

    result = await provider.translate("你好，世界！", "zh-CN", "vi-VN")

    assert result == "Bản dịch truyện mẫu từ Qwen."
    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    payload = call["payload"]
    assert payload["model"] == "qwen/qwen3.8-27b"
    assert payload["temperature"] == 0.3
    messages = payload["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Chinese" in messages[0]["content"]
    assert "Vietnamese" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "你好，世界！"


@pytest.mark.anyio
async def test_strip_markdown_code_fences() -> None:
    fake_http = FakeOpenAIHttp(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "```markdown\nBản dịch không có fence.\n```",
                    }
                }
            ]
        }
    )
    provider = _provider(http=fake_http)
    result = await provider.translate("Some text", "en-US", "vi-VN")
    assert result == "Bản dịch không có fence."


@pytest.mark.anyio
async def test_strip_markdown_code_fences_with_crlf_and_trailing_spaces() -> None:
    fake_http = FakeOpenAIHttp(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "```markdown   \r\nNội dung truyện CRLF.\r\n```   ",
                    }
                }
            ]
        }
    )
    provider = _provider(http=fake_http)
    result = await provider.translate("Some text", "en-US", "vi-VN")
    assert result == "Nội dung truyện CRLF."


@pytest.mark.anyio
async def test_empty_or_whitespace_translation_after_fence_stripping_raises() -> None:
    fake_http = FakeOpenAIHttp(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "```markdown\n   \n```",
                    }
                }
            ]
        }
    )
    provider = _provider(http=fake_http)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Some text", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED
    assert "empty" in str(exc_info.value.message).lower()


@pytest.mark.anyio
async def test_null_message_object_raises_translation_failed() -> None:
    fake_http = FakeOpenAIHttp({"choices": [{"message": None}]})
    provider = _provider(http=fake_http)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Some text", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED


@pytest.mark.anyio
async def test_empty_choices_raises_domain_error() -> None:
    fake_http = FakeOpenAIHttp({"choices": []})
    provider = _provider(http=fake_http)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED


@pytest.mark.anyio
async def test_auto_source_language_rejected_before_http() -> None:
    fake_http = FakeOpenAIHttp()
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Sample", AUTO_SOURCE_LANGUAGE, "vi-VN")

    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE
    assert len(fake_http.calls) == 0


@pytest.mark.anyio
async def test_missing_api_key_raises_configuration_error() -> None:
    provider = _provider(api_key="")
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Sample", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR

    provider_whitespace = _provider(api_key="   ")
    with pytest.raises(DomainError) as exc_info_ws:
        await provider_whitespace.translate("Sample", "en-US", "vi-VN")
    assert exc_info_ws.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR

    provider_none = _provider(api_key=None)
    with pytest.raises(DomainError) as exc_info_none:
        await provider_none.translate("Sample", "en-US", "vi-VN")
    assert exc_info_none.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR


@pytest.mark.anyio
async def test_http_429_rate_limit_error() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = urllib.error.HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=BytesIO(b'{"error": {"message": "Rate limit exceeded"}}'),
    )
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "zh-CN", "vi-VN")

    assert exc_info.value.error_type == ErrorType.PROVIDER_RATE_LIMIT


@pytest.mark.anyio
async def test_http_401_and_403_configuration_error() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = urllib.error.HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=401,
        msg="Unauthorized",
        hdrs=Message(),
        fp=BytesIO(b'{"error": {"message": "Invalid API Key"}}'),
    )
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "zh-CN", "vi-VN")

    assert exc_info.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR


@pytest.mark.anyio
async def test_http_500_translation_failed() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = urllib.error.HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=500,
        msg="Internal Server Error",
        hdrs=Message(),
        fp=BytesIO(b"server error"),
    )
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "zh-CN", "vi-VN")

    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED


@pytest.mark.anyio
async def test_http_unhandled_status_code_maps_to_translation_failed_not_timeout() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = urllib.error.HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=422,
        msg="Unprocessable Entity",
        hdrs=Message(),
        fp=BytesIO(b'{"error": "unprocessable"}'),
    )
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "zh-CN", "vi-VN")

    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED
    assert "HTTP 422" in str(exc_info.value.message)


@pytest.mark.anyio
async def test_timeout_error() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = TimeoutError("Connection timed out")
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "zh-CN", "vi-VN")

    assert exc_info.value.error_type == ErrorType.TIMEOUT


@pytest.mark.anyio
async def test_groq_and_openrouter_subclasses() -> None:
    fake_http = FakeOpenAIHttp()
    groq = GroqTranslationProvider(api_key="gsk_123", http=fake_http)
    assert groq._base_url == "https://api.groq.com/openai/v1"
    assert groq._model == "qwen/qwen3.8-27b"

    await groq.translate("Hello", "en-US", "vi-VN")
    assert fake_http.calls[-1]["extra_headers"] == {}

    openrouter = OpenRouterTranslationProvider(api_key="sk-or-123", http=fake_http)
    assert openrouter._base_url == "https://openrouter.ai/api/v1"
    assert openrouter._model == "openrouter/auto"
    assert openrouter._extra_headers.get("HTTP-Referer") is not None
    assert openrouter._extra_headers.get("X-Title") is not None

    await openrouter.translate("Hello", "en-US", "vi-VN")
    assert fake_http.calls[-1]["extra_headers"] == {
        "HTTP-Referer": "https://github.com/teemo/text-to-audiobook",
        "X-Title": "Text to Audiobook Story Narrator",
    }
