from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from io import BytesIO
from typing import Any

import pytest

from app.domain.chunking import CHUNK_MAX_CHARS
from app.domain.errors import DomainError, ErrorType
from app.domain.languages import AUTO_SOURCE_LANGUAGE
from app.providers.translation.openai_compatible import (
    _MAX_INPUT_CHARS,
    GroqTranslationProvider,
    OpenAICompatibleTranslationProvider,
    OpenRouterTranslationProvider,
    UrllibOpenAIHttpClient,
    _parse_retry_after,
    bcp47_to_openai_lang,
    canonical_groq_model,
    groq_completion_options,
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
    assert openrouter._model == "deepseek/deepseek-v4-flash"
    assert openrouter._extra_headers.get("HTTP-Referer") is not None
    assert openrouter._extra_headers.get("X-Title") is not None

    await openrouter.translate("Hello", "en-US", "vi-VN")
    assert fake_http.calls[-1]["extra_headers"] == {
        "HTTP-Referer": "https://github.com/teemo2Xteemo/text-to-audiobook",
        "X-Title": "Text to Audiobook Story Narrator",
    }


def _http_error(
    code: int,
    body: bytes,
    *,
    retry_after: str | None = None,
    extra_hdrs: dict[str, str] | None = None,
) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    if extra_hdrs is not None:
        for k, v in extra_hdrs.items():
            headers[k] = v
    return urllib.error.HTTPError(
        url="https://api.groq.com/openai/v1/chat/completions",
        code=code,
        msg="error",
        hdrs=headers,
        fp=BytesIO(body),
    )


@pytest.mark.anyio
async def test_groq_rate_limit_reset_header_is_parsed_as_retry_after() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = _http_error(
        429,
        b'{"error": {"message": "Rate limit reached"}}',
        extra_hdrs={"x-ratelimit-reset-tokens": "8.82s"},
    )
    provider = _provider(http=fake_http)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type is ErrorType.PROVIDER_RATE_LIMIT
    assert exc_info.value.retry_after_seconds is not None
    assert 8.8 <= exc_info.value.retry_after_seconds <= 10.0


@pytest.mark.anyio
async def test_groq_rate_limit_error_body_text_is_parsed_as_retry_after() -> None:
    fake_http = FakeOpenAIHttp()
    body = (
        b'{"error": {"message": "Rate limit reached for model qwen/qwen3.8-27b on '
        b"output tokens per minute (OTPM): Limit 1000, Used 955, Requested 147. "
        b'Please try again in 8.82s."}}'
    )
    fake_http.fail = _http_error(429, body)
    provider = _provider(http=fake_http)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type is ErrorType.PROVIDER_RATE_LIMIT
    assert exc_info.value.retry_after_seconds is not None
    assert 8.8 <= exc_info.value.retry_after_seconds <= 10.0


@pytest.mark.anyio
async def test_http_404_includes_provider_message_and_model() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = _http_error(
        404,
        b'{"error": {"message": "model \'missing-model\' does not exist"}}',
    )
    provider = _provider(http=fake_http, model="missing-model")

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "en-US", "vi-VN")

    assert exc_info.value.error_type is ErrorType.PROVIDER_CONFIGURATION_ERROR
    assert "does not exist" in exc_info.value.message
    assert "missing-model" in exc_info.value.message
    assert "HTTP 404" in exc_info.value.message


@pytest.mark.anyio
async def test_http_400_includes_provider_message() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = _http_error(400, b'{"error": {"message": "invalid request parameter"}}')
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "en-US", "vi-VN")

    assert exc_info.value.error_type is ErrorType.TRANSLATION_FAILED
    assert "invalid request parameter" in exc_info.value.message


@pytest.mark.anyio
async def test_http_error_redacts_secrets_from_message() -> None:
    fake_http = FakeOpenAIHttp()
    secret = "gsk_" + ("a" * 24)
    body = json.dumps({"error": {"message": f"rejected key {secret}"}}).encode()
    fake_http.fail = _http_error(401, body)
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "en-US", "vi-VN")

    assert exc_info.value.error_type is ErrorType.PROVIDER_CONFIGURATION_ERROR
    assert secret not in exc_info.value.message
    assert "[redacted]" in exc_info.value.message


@pytest.mark.anyio
async def test_connection_refused_is_not_a_timeout() -> None:
    fake_http = FakeOpenAIHttp()
    fake_http.fail = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "en-US", "vi-VN")

    assert exc_info.value.error_type is ErrorType.TRANSLATION_FAILED
    assert exc_info.value.error_type is not ErrorType.TIMEOUT
    assert "connection failed" in exc_info.value.message


@pytest.mark.anyio
async def test_socket_timeout_urlerror_is_timeout() -> None:
    fake_http = FakeOpenAIHttp()
    # socket.timeout is TimeoutError on Python 3.10+.
    fake_http.fail = urllib.error.URLError(TimeoutError("timed out"))
    provider = _provider(http=fake_http)

    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "en-US", "vi-VN")

    assert exc_info.value.error_type is ErrorType.TIMEOUT


@pytest.mark.anyio
async def test_non_json_success_body_is_domain_error() -> None:
    class _RawClient:
        async def create_chat_completion(
            self,
            *,
            payload: dict[str, Any],
            api_key: str,
            extra_headers: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            del payload, api_key, extra_headers
            raise json.JSONDecodeError("Expecting value", "not-json", 0)

    provider = _provider(http=_RawClient())  # type: ignore[arg-type]
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Text", "en-US", "vi-VN")
    assert exc_info.value.error_type is ErrorType.TRANSLATION_FAILED
    assert not isinstance(exc_info.value, json.JSONDecodeError)


def test_chunk_limit_fits_openai_compatible_context() -> None:
    assert CHUNK_MAX_CHARS < _MAX_INPUT_CHARS


@pytest.mark.anyio
async def test_context_size_guard_is_invalid_input_and_not_retried() -> None:
    from app.application.pipeline.retry import with_chunk_retry
    from app.domain.retry import RetryPolicy

    provider = _provider()
    text = "a" * (_MAX_INPUT_CHARS + 1)
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        return await provider.translate(text, "en-US", "vi-VN")

    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    with pytest.raises(DomainError) as exc_info:
        await with_chunk_retry(
            _op,
            policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
            sleep=_sleep,
            job_id="job-1",
            chunk_id="chunk-1",
            stage="translated",
        )

    assert exc_info.value.error_type is ErrorType.INVALID_INPUT
    assert calls == 1
    assert slept == []


def test_retry_after_delta_is_capped_and_http_date_is_honored() -> None:
    assert _parse_retry_after("30") == 30.0
    assert _parse_retry_after("  120 ") == 60.0
    assert _parse_retry_after("-5") == 0.0
    assert _parse_retry_after("soon") is None
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    parsed = _parse_retry_after("Tue, 27 Oct 2026 12:00:15 GMT", now=now)
    assert parsed is not None
    assert 14 <= parsed <= 16


@pytest.mark.anyio
async def test_retry_after_extends_chunk_backoff() -> None:
    from app.application.pipeline.retry import with_chunk_retry
    from app.domain.retry import RetryPolicy

    fake_http = FakeOpenAIHttp()
    fake_http.fail = _http_error(429, b'{"error": {"message": "slow down"}}', retry_after="25")
    provider = _provider(http=fake_http)
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        return await provider.translate("Text", "en-US", "vi-VN")

    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    with pytest.raises(DomainError) as exc_info:
        await with_chunk_retry(
            _op,
            policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
            sleep=_sleep,
            job_id="job-1",
            chunk_id="chunk-1",
            stage="translated",
        )

    assert exc_info.value.error_type is ErrorType.PROVIDER_RATE_LIMIT
    assert calls == 2
    assert slept == [25.0]
    assert exc_info.value.retry_after_seconds == 25.0


def test_urllib_client_joins_url_and_sends_auth_timeout_and_extra_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def read(self) -> bytes:
            return b'{"id": "ok", "choices": [], "model": "served-model"}'

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["method"] = request.get_method()
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        captured["body"] = request.data
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = UrllibOpenAIHttpClient(
        base_url="https://openrouter.ai/api/v1/",
        timeout_seconds=12.5,
        provider_name="openrouter",
    )
    result = asyncio.run(
        client.create_chat_completion(
            payload={"model": "openrouter/auto"},
            api_key="sk-or-test",
            extra_headers={
                "HTTP-Referer": "https://github.com/teemo2Xteemo/text-to-audiobook",
                "X-Title": "Text to Audiobook Story Narrator",
            },
        )
    )
    assert result["model"] == "served-model"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["timeout"] == 12.5
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer sk-or-test"
    assert (
        captured["headers"]["http-referer"] == "https://github.com/teemo2Xteemo/text-to-audiobook"
    )
    assert captured["headers"]["x-title"] == "Text to Audiobook Story Narrator"
    assert json.loads(captured["body"])["model"] == "openrouter/auto"


def test_urllib_client_non_json_body_raises_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: _Response())
    client = UrllibOpenAIHttpClient(base_url="https://api.groq.com/openai/v1", timeout_seconds=5)
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(
            client.create_chat_completion(
                payload={"model": "qwen/qwen3.8-27b"},
                api_key="gsk_test",
            )
        )
    assert exc_info.value.error_type is ErrorType.TRANSLATION_FAILED
    assert "non-JSON" in exc_info.value.message


def test_groq_completion_options_for_gpt_oss_and_qwen() -> None:
    gpt = groq_completion_options("openai/gpt-oss-120b")
    assert gpt["reasoning_effort"] == "low"
    assert gpt["include_reasoning"] is False
    assert gpt["max_completion_tokens"] >= 4096
    assert "reasoning_format" not in gpt

    qwen = groq_completion_options("qwen/qwen3.8-27b")
    assert qwen["reasoning_effort"] == "none"
    assert "include_reasoning" not in qwen
    assert "reasoning_format" not in qwen
    assert qwen["max_completion_tokens"] >= 4096

    assert groq_completion_options("llama-3.3-70b-versatile") == {}
    assert canonical_groq_model("llama-3.3-70b-versatile") == "openai/gpt-oss-120b"
    assert canonical_groq_model("llama-3.1-8b-instant") == "openai/gpt-oss-20b"
    assert canonical_groq_model("qwen/qwen3.8-27b") == "qwen/qwen3.8-27b"


@pytest.mark.anyio
async def test_groq_qwen_disables_reasoning_and_gpt_oss_hides_it() -> None:
    qwen_http = FakeOpenAIHttp()
    qwen = GroqTranslationProvider(api_key="gsk_test", http=qwen_http)
    await qwen.translate("Hello", "en-US", "vi-VN")
    qwen_payload = qwen_http.calls[-1]["payload"]
    assert qwen_payload["model"] == "qwen/qwen3.8-27b"
    assert qwen_payload["reasoning_effort"] == "none"
    assert "max_completion_tokens" in qwen_payload
    assert "max_tokens" not in qwen_payload
    assert "reasoning_format" not in qwen_payload
    assert "include_reasoning" not in qwen_payload

    oss_http = FakeOpenAIHttp()
    oss = GroqTranslationProvider(
        api_key="gsk_test",
        model="llama-3.3-70b-versatile",
        http=oss_http,
    )
    assert oss._model == "openai/gpt-oss-120b"
    await oss.translate("Hello", "en-US", "vi-VN")
    oss_payload = oss_http.calls[-1]["payload"]
    assert oss_payload["model"] == "openai/gpt-oss-120b"
    assert oss_payload["reasoning_effort"] == "low"
    assert oss_payload["include_reasoning"] is False
    assert "max_completion_tokens" in oss_payload
    assert "max_tokens" not in oss_payload
    assert "reasoning_format" not in oss_payload

    small = GroqTranslationProvider(api_key="gsk_test", model="llama-3.1-8b-instant")
    assert small._model == "openai/gpt-oss-20b"

    router_http = FakeOpenAIHttp()
    router = OpenRouterTranslationProvider(
        api_key="sk-or-test",
        model="openrouter/auto",
        http=router_http,
    )
    await router.translate("Hello", "en-US", "vi-VN")
    router_payload = router_http.calls[-1]["payload"]
    assert "reasoning" not in router_payload
    assert "reasoning_effort" not in router_payload
    assert "include_reasoning" not in router_payload
    assert "reasoning_format" not in router_payload


@pytest.mark.anyio
async def test_reasoning_trace_is_not_used_as_the_translation() -> None:
    fake_http = FakeOpenAIHttp(
        {
            "model": "openai/gpt-oss-120b",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "<think>internal plan</think>\nBản dịch sạch.",
                        "reasoning": "internal plan that must not be spoken",
                    }
                }
            ],
        }
    )
    provider = GroqTranslationProvider(
        api_key="gsk_test",
        model="openai/gpt-oss-120b",
        http=fake_http,
    )
    result = await provider.translate("Hello", "en-US", "vi-VN")
    assert result == "Bản dịch sạch."
    assert "internal plan" not in result
    assert "<think>" not in result


@pytest.mark.anyio
async def test_reasoning_only_response_is_empty_translation() -> None:
    fake_http = FakeOpenAIHttp(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "reasoning": "only the trace",
                    }
                }
            ]
        }
    )
    provider = GroqTranslationProvider(api_key="gsk_test", http=fake_http)
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type is ErrorType.TRANSLATION_FAILED
    assert "only the trace" not in exc_info.value.message


def test_groq_completion_budget_covers_a_max_chunk() -> None:
    from app.domain.chunking import CHUNK_MAX_CHARS

    budget = groq_completion_options("openai/gpt-oss-120b")["max_completion_tokens"]
    # Several tokens per source character: translation expansion plus low reasoning.
    assert budget >= CHUNK_MAX_CHARS * 4
    assert groq_completion_options("qwen/qwen3-future") == {}


def _completion(
    content: str,
    finish_reason: object = "stop",
    *,
    include_reason: bool = True,
) -> dict:
    choice: dict[str, object] = {"message": {"role": "assistant", "content": content}}
    if include_reason:
        choice["finish_reason"] = finish_reason
    return {"choices": [choice]}


@pytest.mark.anyio
async def test_length_finish_reason_is_not_returned_or_cached(tmp_path) -> None:
    from app.application.pipeline.artifact_cache import CacheIdentity, PipelineArtifactCache
    from app.application.pipeline.retry import with_chunk_retry
    from app.domain.retry import RetryPolicy, is_retryable
    from app.infrastructure.artifact_cache_fs import FilesystemArtifactCache

    partial = "partial translation that must not be spoken"
    fake_http = FakeOpenAIHttp(_completion(partial, "length"))
    provider = GroqTranslationProvider(
        api_key="gsk_test",
        model="openai/gpt-oss-120b",
        http=fake_http,
    )
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        return await provider.translate("A long chunk of story text.", "en-US", "vi-VN")

    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    with pytest.raises(DomainError) as exc_info:
        await with_chunk_retry(
            _op,
            policy=RetryPolicy(max_attempts=3, backoff_seconds=1.0),
            sleep=_sleep,
            job_id="job-1",
            chunk_id="chunk-001",
            stage="translated",
        )

    err = exc_info.value
    assert err.error_type is ErrorType.INVALID_INPUT
    assert not is_retryable(err.error_type)
    assert "model output was cut off" in err.message
    assert "invalid" not in err.message.lower()
    assert "openai/gpt-oss-120b" in err.message
    assert partial not in err.message
    assert calls == 1
    assert slept == []

    cache = PipelineArtifactCache(
        FilesystemArtifactCache(tmp_path / "cache"),
        CacheIdentity(
            translation_provider="groq",
            translation_model="openai/gpt-oss-120b",
            tts_provider="fake",
            tts_model="fake",
        ),
    )
    destination = tmp_path / "chunk-001.translated.txt"
    assert (
        cache.fill_translation(
            text="A long chunk of story text.",
            source_language="en-US",
            target_language="vi-VN",
            destination=destination,
            job_id="job-1",
            chunk_id="chunk-001",
        )
        is False
    )
    assert not destination.exists()


@pytest.mark.anyio
async def test_stop_finish_reason_returns_content() -> None:
    fake_http = FakeOpenAIHttp(_completion("Bản dịch đủ.", "stop"))
    provider = _provider(http=fake_http)
    assert await provider.translate("Hello", "en-US", "vi-VN") == "Bản dịch đủ."


@pytest.mark.anyio
async def test_missing_finish_reason_still_returns_content() -> None:
    fake_http = FakeOpenAIHttp(_completion("Bản dịch cũ.", include_reason=False))
    provider = _provider(http=fake_http)
    assert await provider.translate("Hello", "en-US", "vi-VN") == "Bản dịch cũ."

    null_reason = FakeOpenAIHttp(_completion("Vẫn đủ.", None))
    provider_null = _provider(http=null_reason)
    assert await provider_null.translate("Hello", "en-US", "vi-VN") == "Vẫn đủ."


@pytest.mark.anyio
async def test_content_filter_finish_reason_is_not_a_translation() -> None:
    fake_http = FakeOpenAIHttp(_completion("blocked fragment", "content_filter"))
    provider = _provider(http=fake_http, model="openai/gpt-oss-20b")
    with pytest.raises(DomainError) as exc_info:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert exc_info.value.error_type is ErrorType.INVALID_INPUT
    assert "content_filter" in exc_info.value.message
    assert "blocked fragment" not in exc_info.value.message


@pytest.mark.anyio
async def test_http_503_honors_retry_after() -> None:
    from app.application.pipeline.retry import with_chunk_retry
    from app.domain.retry import RetryPolicy

    fake_http = FakeOpenAIHttp()
    fake_http.fail = _http_error(503, b'{"error": {"message": "upstream busy"}}', retry_after="12")
    provider = _provider(http=fake_http)
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return await provider.translate("Text", "en-US", "vi-VN")
        fake_http.fail = None
        return await provider.translate("Text", "en-US", "vi-VN")

    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    result = await with_chunk_retry(
        _op,
        policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
        sleep=_sleep,
        job_id="job-1",
        chunk_id="chunk-1",
        stage="translated",
    )
    assert result
    assert calls == 2
    assert slept == [12.0]


@pytest.mark.anyio
async def test_openrouter_deepseek_disables_reasoning_only_for_that_model() -> None:
    from app.providers.translation.openai_compatible import OPENROUTER_DEEPSEEK_V4_FLASH

    deepseek_http = FakeOpenAIHttp()
    deepseek = OpenRouterTranslationProvider(
        api_key="sk-or-test",
        model=OPENROUTER_DEEPSEEK_V4_FLASH,
        http=deepseek_http,
    )
    await deepseek.translate("Hello", "en-US", "vi-VN")
    payload = deepseek_http.calls[-1]["payload"]
    assert payload["model"] == OPENROUTER_DEEPSEEK_V4_FLASH
    assert payload["reasoning"] == {"effort": "none"}
    assert "reasoning_effort" not in payload
    assert payload["max_tokens"] == 8192
    assert "max_completion_tokens" not in payload
    assert "provider" not in payload

    other_http = FakeOpenAIHttp()
    other = OpenRouterTranslationProvider(
        api_key="sk-or-test",
        model="openrouter/auto",
        http=other_http,
    )
    await other.translate("Hello", "en-US", "vi-VN")
    other_payload = other_http.calls[-1]["payload"]
    assert "reasoning" not in other_payload
    assert "reasoning_effort" not in other_payload


@pytest.mark.anyio
async def test_openrouter_error_finish_reason_is_retried() -> None:
    from app.application.pipeline.retry import with_chunk_retry
    from app.domain.retry import RetryPolicy, is_retryable

    fake_http = FakeOpenAIHttp(_completion("upstream glitch", "error"))
    provider = OpenRouterTranslationProvider(
        api_key="sk-or-test",
        model="deepseek/deepseek-v4-flash",
        http=fake_http,
    )
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return await provider.translate("Hello", "en-US", "vi-VN")
        fake_http.response_data = _completion("Bản dịch đủ.", "stop")
        return await provider.translate("Hello", "en-US", "vi-VN")

    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    with pytest.raises(DomainError) as first:
        await provider.translate("Hello", "en-US", "vi-VN")
    assert first.value.error_type is ErrorType.TRANSLATION_FAILED
    assert is_retryable(first.value.error_type)
    assert "upstream glitch" not in first.value.message

    calls = 0
    result = await with_chunk_retry(
        _op,
        policy=RetryPolicy(max_attempts=2, backoff_seconds=1.0),
        sleep=_sleep,
        job_id="job-1",
        chunk_id="chunk-1",
        stage="translated",
    )
    assert result == "Bản dịch đủ."
    assert calls == 2
    assert slept == [1.0]
