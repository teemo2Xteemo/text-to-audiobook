from dataclasses import dataclass

from app.domain.errors import ErrorType

RETRYABLE_ERROR_TYPES = frozenset(
    {
        ErrorType.TRANSLATION_FAILED,
        ErrorType.TTS_FAILED,
        ErrorType.PROVIDER_RATE_LIMIT,
        ErrorType.TIMEOUT,
    }
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    backoff_seconds: float
    rate_limit_max_attempts: int = 6
    rate_limit_backoff_seconds: float = 5.0
    max_wait_seconds: float = 120.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds must be >= 0")
        if self.rate_limit_max_attempts < 1:
            raise ValueError("rate_limit_max_attempts must be >= 1")
        if self.rate_limit_backoff_seconds < 0:
            raise ValueError("rate_limit_backoff_seconds must be >= 0")
        if self.max_wait_seconds < 0:
            raise ValueError("max_wait_seconds must be >= 0")


def is_retryable(error_type: ErrorType) -> bool:
    return error_type in RETRYABLE_ERROR_TYPES


def delay_for(failed_attempt_index: int, policy: RetryPolicy) -> float:
    if failed_attempt_index < 0:
        raise ValueError("failed_attempt_index must be >= 0")
    return policy.backoff_seconds * (2**failed_attempt_index)
