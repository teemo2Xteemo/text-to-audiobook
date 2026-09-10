from __future__ import annotations

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
            self._queue.enqueue(
                RQ_PROCESS_JOB,
                job_id,
                job_timeout=self._job_timeout,
                on_failure=Callback(RQ_ON_FAILURE),
            )
        except Exception as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to enqueue job") from exc
