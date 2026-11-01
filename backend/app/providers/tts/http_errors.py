from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from app.domain.errors import DomainError, ErrorType
from app.providers.http_retry import parse_retry_after, retry_after_header


def map_tts_http_error(exc: Exception) -> DomainError:
    """Shared HTTP/transport error mapping for network TTS adapters."""
    if isinstance(exc, DomainError):
        return exc
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        # Static messages only: provider bodies can echo story text.
        if code in (400, 413, 422):
            # The provider rejected this request; retrying the same text cannot succeed.
            return DomainError(
                ErrorType.INVALID_INPUT, f"tts provider rejected the request (HTTP {code})"
            )
        if code == 404:
            return DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR, "tts voice or endpoint not found (HTTP 404)"
            )
        if code == 402:
            return DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR,
                "tts provider quota or plan limit reached (HTTP 402)",
            )
        if code in (401, 403):
            return DomainError(ErrorType.PROVIDER_CONFIGURATION_ERROR, f"tts HTTP {code}")
        if code == 429:
            return DomainError(
                ErrorType.PROVIDER_RATE_LIMIT,
                "tts rate limited",
                retry_after_seconds=parse_retry_after(retry_after_header(exc)),
            )
        return DomainError(ErrorType.TTS_FAILED, f"tts HTTP {code}")
    if isinstance(exc, TimeoutError) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)
    ):
        return DomainError(ErrorType.TIMEOUT, "tts request timed out")
    if isinstance(exc, urllib.error.URLError):
        return DomainError(ErrorType.TTS_FAILED, "tts connection failed")
    return DomainError(ErrorType.TTS_FAILED, "tts failed")


def open_bytes(
    opener: Callable[..., Any], request: urllib.request.Request, timeout: float
) -> bytes:
    """Blocking request via ``opener``; always closes error responses. Run in a thread."""
    try:
        with opener(request, timeout=timeout) as response:
            data: bytes = response.read()
            return data
    except urllib.error.HTTPError as exc:
        exc.close()
        raise
