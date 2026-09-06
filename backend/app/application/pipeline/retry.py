from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.domain.errors import DomainError
from app.domain.retry import RetryPolicy, delay_for, is_retryable

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
    last_error: DomainError | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await operation()
        except DomainError as exc:
            last_error = exc
            can_retry = attempt + 1 < policy.max_attempts and is_retryable(exc.error_type)
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
                },
            )
            await sleep(delay_for(attempt, policy))
    assert last_error is not None
    raise last_error
