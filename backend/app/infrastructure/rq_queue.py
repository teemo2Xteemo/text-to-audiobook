from __future__ import annotations

from rq import Queue

from app.domain.errors import DomainError, ErrorType

RQ_PROCESS_JOB = "app.workers.process_job"
RQ_QUEUE_NAME = "jobs"


class RQJobQueue:
    def __init__(self, queue: Queue) -> None:
        self._queue = queue

    async def enqueue(self, job_id: str) -> None:
        try:
            self._queue.enqueue(RQ_PROCESS_JOB, job_id)
        except Exception as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to enqueue job") from exc
