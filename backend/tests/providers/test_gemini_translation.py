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
async def test_translate_missing_api_key_raises_provider_config_error() -> None:
    provider = _provider(api_key=None)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR
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
@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
async def test_translate_auth_client_errors_mapped_to_config_error(status_code: int) -> None:
    fake_http = FakeGeminiHttp()
    err = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com",
        code=status_code,
        msg="Error",
        hdrs=Message(),
        fp=BytesIO(b'{"error": {"message": "Invalid API key"}}'),
    )
    fake_http.fail = err
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR


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
async def test_translate_prompt_feedback_block_mapped_to_invalid_input() -> None:
    fake_http = FakeGeminiHttp(
        response_data={
            "promptFeedback": {"blockReason": "SAFETY"},
            "candidates": [],
        }
    )
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.INVALID_INPUT
    assert "prompt blocked" in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize("finish_reason", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT", "SPII"])
async def test_translate_safety_filter_block(finish_reason: str) -> None:
    fake_http = FakeGeminiHttp(
        response_data={
            "candidates": [
                {
                    "finishReason": finish_reason,
                    "content": {"parts": [], "role": "model"},
                }
            ]
        }
    )
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type == ErrorType.INVALID_INPUT


@pytest.mark.anyio
async def test_translate_multipart_response_skips_thoughts() -> None:
    fake_http = FakeGeminiHttp(
        response_data={
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"thought": True, "text": "Analyzing the input text..."},
                            {"text": "Đoạn dịch thứ nhất. "},
                            {"text": "Đoạn dịch thứ hai."},
                        ],
                        "role": "model",
                    },
                }
            ]
        }
    )
    provider = _provider(http=fake_http)
    result = await provider.translate("First sentence. Second sentence.", "en-US", "vi-VN")
    assert result == "Đoạn dịch thứ nhất. Đoạn dịch thứ hai."


@pytest.mark.anyio
async def test_with_chunk_retry_does_not_retry_deterministic_errors() -> None:
    from app.application.pipeline.retry import with_chunk_retry
    from app.domain.retry import RetryPolicy

    fake_http = FakeGeminiHttp()
    err = urllib.error.HTTPError(
        url="https://generativelanguage.googleapis.com",
        code=403,
        msg="Forbidden",
        hdrs=Message(),
        fp=BytesIO(b'{"error": {"message": "API_KEY_INVALID"}}'),
    )
    fake_http.fail = err
    provider = _provider(http=fake_http)

    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        return await provider.translate("Hello", "en-US", "vi-VN")

    sleep_calls: list[float] = []

    async def _fake_sleep(d: float) -> None:
        sleep_calls.append(d)

    with pytest.raises(DomainError) as exc_info:
        await with_chunk_retry(
            _op,
            policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
            sleep=_fake_sleep,
            job_id="job-1",
            chunk_id="chunk-1",
            stage="translated",
        )

    assert exc_info.value.error_type == ErrorType.PROVIDER_CONFIGURATION_ERROR
    assert calls == 1
    assert sleep_calls == []


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


@pytest.mark.anyio
async def test_translate_strips_markdown_code_fences_with_crlf_and_spaces() -> None:
    fake_http = FakeGeminiHttp(
        response_data={
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [{"text": "```markdown  \r\nTruyện CRLF sạch sẽ.\r\n```  "}],
                        "role": "model",
                    },
                }
            ]
        }
    )
    provider = _provider(http=fake_http)
    result = await provider.translate("Story chapter", "en-US", "vi-VN")
    assert result == "Truyện CRLF sạch sẽ."
