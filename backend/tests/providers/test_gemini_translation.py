from __future__ import annotations

import urllib.error
from email.message import Message
from io import BytesIO
from typing import Any

import pytest

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import AUTO_SOURCE_LANGUAGE
from app.providers.translation.gemini import (
    GeminiTranslationProvider,
    bcp47_to_gemini,
)


class FakeGeminiHttp:
    def __init__(self, response_data: dict[str, Any] | None = None) -> None:
        self.response_data = (
            response_data
            if response_data is not None
            else {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Bản dịch truyện mẫu."}],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ]
            }
        )
        self.calls: list[dict[str, Any]] = []
        self.fail: BaseException | None = None

    async def generate_content(
        self, *, model: str, payload: dict[str, Any], api_key: str
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "payload": payload, "api_key": api_key})
        if self.fail is not None:
            raise self.fail
        return self.response_data


def _provider(
    http: FakeGeminiHttp | None = None,
    api_key: str | None = "test-gemini-key",
) -> GeminiTranslationProvider:
    return GeminiTranslationProvider(
        api_key=api_key,
        model="gemini-3.5-flash",
        base_url="https://generativelanguage.googleapis.com",
        timeout_seconds=60.0,
        http=http if http is not None else FakeGeminiHttp(),
    )


def test_bcp47_mapping_valid_and_unsupported() -> None:
    code, name = bcp47_to_gemini("zh-CN")
    assert code == "zh-Hans"
    assert name == "Chinese"

    code_vi, name_vi = bcp47_to_gemini("vi-VN")
    assert code_vi == "vi"
    assert name_vi == "Vietnamese"

    with pytest.raises(DomainError) as exc_info:
        bcp47_to_gemini("xx-YY")
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE


def test_supported_languages_list() -> None:
    provider = _provider()
    langs = provider.supported_languages()
    assert "zh-CN" in langs
    assert "vi-VN" in langs
    assert "en-US" in langs
    assert "ja-JP" in langs


@pytest.mark.anyio
async def test_translate_success() -> None:
    fake_http = FakeGeminiHttp()
    provider = _provider(http=fake_http)

    result = await provider.translate("春天到了。", "zh-CN", "vi-VN")

    assert result == "Bản dịch truyện mẫu."
    assert len(fake_http.calls) == 1
    call = fake_http.calls[0]
    assert call["model"] == "gemini-3.5-flash"
    assert call["api_key"] == "test-gemini-key"

    payload = call["payload"]
    assert "contents" in payload
    assert payload["contents"][0]["parts"][0]["text"] == "春天到了。"
    assert "systemInstruction" in payload
    instruction = payload["systemInstruction"]["parts"][0]["text"]
    assert "Chinese" in instruction
    assert "Vietnamese" in instruction


@pytest.mark.anyio
async def test_translate_missing_api_key_raises_translation_failed() -> None:
    provider = _provider(api_key=None)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED
    assert "GEMINI_API_KEY" in str(exc_info.value)


@pytest.mark.anyio
async def test_translate_auto_source_rejected() -> None:
    provider = _provider()
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", AUTO_SOURCE_LANGUAGE, "vi-VN")
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE


@pytest.mark.anyio
async def test_translate_text_exceeds_context() -> None:
    provider = _provider()
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("x" * 10001, "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED
    assert "exceeds" in str(exc_info.value)


@pytest.mark.anyio
async def test_translate_rate_limit_429() -> None:
    fake_http = FakeGeminiHttp()
    err = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com",
        code=429,
        msg="Too Many Requests",
        hdrs=Message(),
        fp=BytesIO(b'{"error": {"message": "Resource exhausted"}}'),
    )
    fake_http.fail = err
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.PROVIDER_RATE_LIMIT


@pytest.mark.anyio
async def test_translate_safety_filter_block() -> None:
    fake_http = FakeGeminiHttp(
        response_data={
            "candidates": [
                {
                    "finishReason": "SAFETY",
                    "content": {"parts": [], "role": "model"},
                }
            ]
        }
    )
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.TRANSLATION_FAILED


@pytest.mark.anyio
async def test_translate_timeout_mapped() -> None:
    fake_http = FakeGeminiHttp()
    fake_http.fail = TimeoutError("timed out")
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.TIMEOUT


@pytest.mark.anyio
async def test_translate_strips_markdown_code_fences() -> None:
    fake_http = FakeGeminiHttp(
        response_data={
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [{"text": "```markdown\nTruyện đã dịch sạch sẽ.\n```"}],
                        "role": "model",
                    },
                }
            ]
        }
    )
    provider = _provider(http=fake_http)
    result = await provider.translate("Story chapter", "en-US", "vi-VN")
    assert result == "Truyện đã dịch sạch sẽ."
