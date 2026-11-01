from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.domain.errors import DomainError, ErrorType
from app.domain.retry import RetryPolicy, is_retryable

logger = logging.getLogger(__name__)


async def with_chunk_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], Awaitable[None]],
    job_id: str,
    chunk_id: str,
    stage: str,
) -> T:
    attempt = 0
    while True:
        try:
            return await operation()
        except DomainError as exc:
            rate_limited = exc.error_type is ErrorType.PROVIDER_RATE_LIMIT
            max_attempts = policy.rate_limit_max_attempts if rate_limited else policy.max_attempts
            backoff = policy.rate_limit_backoff_seconds if rate_limited else policy.backoff_seconds
            can_retry = attempt + 1 < max_attempts and is_retryable(exc.error_type)
            if not can_retry:
                raise
            retry_count = attempt + 1
            logger.info(
                "pipeline_chunk_retry",
                extra={
                    "job_id": job_id,
                    "chunk_id": chunk_id,
                    "retry_count": retry_count,
                    "error_type": exc.error_type.value,
                    "stage": stage,
                    "retry_after_seconds": exc.retry_after_seconds,
                },
            )
            wait = backoff * (2**attempt)
            hint = exc.retry_after_seconds
            if hint is not None:
                wait = max(wait, hint)
            await sleep(min(wait, policy.max_wait_seconds))
            attempt += 1
