import pytest

from app.domain.errors import ErrorType
from app.domain.retry import RetryPolicy, delay_for, is_retryable


def test_retryable_error_types() -> None:
    assert is_retryable(ErrorType.TRANSLATION_FAILED)
    assert is_retryable(ErrorType.TTS_FAILED)
    assert is_retryable(ErrorType.PROVIDER_RATE_LIMIT)
    assert is_retryable(ErrorType.TIMEOUT)


def test_non_retryable_error_types() -> None:
    assert not is_retryable(ErrorType.INVALID_INPUT)
    assert not is_retryable(ErrorType.UNSUPPORTED_LANGUAGE)
    assert not is_retryable(ErrorType.AUDIO_PROCESSING_FAILED)
    assert not is_retryable(ErrorType.STORAGE_FAILED)


def test_delay_sequence_uses_exponential_base() -> None:
    policy = RetryPolicy(max_attempts=3, backoff_seconds=1.0)
    assert delay_for(0, policy) == 1.0
    assert delay_for(1, policy) == 2.0
    assert delay_for(2, policy) == 4.0


def test_rejects_invalid_policy() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        RetryPolicy(max_attempts=0, backoff_seconds=1.0)
    with pytest.raises(ValueError, match="backoff_seconds"):
        RetryPolicy(max_attempts=3, backoff_seconds=-1.0)


def test_delay_for_rejects_negative_attempt() -> None:
    policy = RetryPolicy(max_attempts=3, backoff_seconds=1.0)
    with pytest.raises(ValueError, match="failed_attempt_index"):
        delay_for(-1, policy)
