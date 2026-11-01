from __future__ import annotations

import pytest

from app.application.pipeline.retry import with_chunk_retry
from app.domain.errors import DomainError, ErrorType
from app.domain.retry import RetryPolicy

_POLICY = RetryPolicy(
    max_attempts=3,
    backoff_seconds=1.0,
    rate_limit_max_attempts=6,
    rate_limit_backoff_seconds=5.0,
)


async def _run(error: DomainError) -> list[float]:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    async def op() -> None:
        raise error

    with pytest.raises(DomainError):
        await with_chunk_retry(
            op, policy=_POLICY, sleep=sleep, job_id="j", chunk_id="c", stage="tts"
        )
    return waits


@pytest.mark.anyio
async def test_rate_limit_uses_rate_limit_policy() -> None:
    waits = await _run(DomainError(ErrorType.PROVIDER_RATE_LIMIT, "busy"))
    assert waits == [5.0, 10.0, 20.0, 40.0, 80.0]


@pytest.mark.anyio
async def test_retry_after_hint_capped() -> None:
    err = DomainError(ErrorType.PROVIDER_RATE_LIMIT, "busy", retry_after_seconds=500)
    assert (await _run(err))[0] == 120.0


@pytest.mark.anyio
async def test_other_errors_keep_base_policy() -> None:
    waits = await _run(DomainError(ErrorType.TTS_FAILED, "boom"))
    assert waits == [1.0, 2.0]
