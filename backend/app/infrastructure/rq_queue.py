from __future__ import annotations

import asyncio

from rq import Queue
from rq.job import Callback

from app.domain.errors import DomainError, ErrorType

RQ_PROCESS_JOB = "app.workers.process_job"
RQ_ON_FAILURE = "app.workers.rq_failure.handle_rq_failure"
RQ_QUEUE_NAME = "jobs"


class RQJobQueue:
    def __init__(self, queue: Queue, *, job_timeout: int) -> None:
        self._queue = queue
        self._job_timeout = job_timeout

    async def enqueue(self, job_id: str) -> None:
        try:
            await asyncio.to_thread(self._enqueue_sync, job_id)
        except Exception as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to enqueue job") from exc

    def _enqueue_sync(self, job_id: str) -> None:
        rq_job_id = f"process-{job_id}"
        fetch_job = getattr(self._queue, "fetch_job", None)
        if callable(fetch_job):
            existing = fetch_job(rq_job_id)
            if existing is not None and existing.get_status() in {"queued", "started"}:
                return
        self._queue.enqueue(
            RQ_PROCESS_JOB,
            job_id,
            job_id=rq_job_id,
            job_timeout=self._job_timeout,
            on_failure=Callback(RQ_ON_FAILURE),
        )
