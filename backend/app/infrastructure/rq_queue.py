from __future__ import annotations

import asyncio
import uuid

from rq import Queue
from rq.job import Callback

from app.domain.errors import DomainError, ErrorType

RQ_PROCESS_JOB = "app.workers.process_job"
RQ_ON_FAILURE = "app.workers.rq_failure.handle_rq_failure"
RQ_ON_SUCCESS = "app.workers.rq_continue.handle_rq_success"
RQ_QUEUE_NAME = "jobs"

_REGISTRIES = (
    "started_job_registry",
    "failed_job_registry",
    "finished_job_registry",
    "deferred_job_registry",
    "scheduled_job_registry",
)


def rq_process_job_id(job_id: str, run_count: int) -> str:
    """Per-run RQ id. The worker is still bookkeeping the previous id when we enqueue."""
    return f"process-{job_id}-r{run_count}"


def _matches_domain(rq_job_id: str, job_id: str) -> bool:
    prefix = f"process-{job_id}"
    return rq_job_id == prefix or rq_job_id.startswith(prefix + "-")


class RQJobQueue:
    def __init__(self, queue: Queue, *, job_timeout: int, result_ttl: int | None = None) -> None:
        self._queue = queue
        self._job_timeout = job_timeout
        self._result_ttl = result_ttl

    async def enqueue(
        self,
        job_id: str,
        *,
        run_count: int = 0,
        keep_running: bool = False,
    ) -> None:
        try:
            await asyncio.to_thread(self._enqueue_sync, job_id, run_count, keep_running)
        except Exception as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to enqueue job") from exc

    def _enqueue_sync(self, job_id: str, run_count: int, keep_running: bool) -> None:
        if self._has_queued(job_id, run_count):
            return
        if not keep_running:
            # Crash recovery and user retry drop stale started/failed entries.
            # Continuation must not: the callback runs while that job is still started,
            # and deleting it lets the worker's success path expire the replacement.
            self._drop_not_queued(job_id, run_count)
        rq_job_id = rq_process_job_id(job_id, run_count)
        existing = self._fetch(rq_job_id)
        if existing is not None and _job_status(existing) == "queued":
            return
        if existing is not None:
            if keep_running:
                rq_job_id = f"{rq_job_id}-{uuid.uuid4().hex[:8]}"
            else:
                existing.delete()
        payload: dict[str, object] = {
            "job_id": rq_job_id,
            "job_timeout": self._job_timeout,
            "on_failure": Callback(RQ_ON_FAILURE),
            "on_success": Callback(RQ_ON_SUCCESS),
        }
        if self._result_ttl is not None:
            payload["result_ttl"] = self._result_ttl
        self._queue.enqueue(RQ_PROCESS_JOB, job_id, **payload)

    def _has_queued(self, job_id: str, run_count: int) -> bool:
        for rq_id in self._candidate_ids(job_id, run_count):
            existing = self._fetch(rq_id)
            if existing is not None and _job_status(existing) == "queued":
                return True
        return False

    def _drop_not_queued(self, job_id: str, run_count: int) -> None:
        for rq_id in self._candidate_ids(job_id, run_count):
            existing = self._fetch(rq_id)
            if existing is None or _job_status(existing) == "queued":
                continue
            existing.delete()

    def _candidate_ids(self, job_id: str, run_count: int) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def add(value: object) -> None:
            text = str(value)
            if text in seen or not _matches_domain(text, job_id):
                return
            seen.add(text)
            ordered.append(text)

        add(f"process-{job_id}")
        add(rq_process_job_id(job_id, run_count))
        job_ids = getattr(self._queue, "job_ids", None)
        if job_ids:
            for rq_id in list(job_ids):
                add(rq_id)
        # Queued ids plus registry membership. FinishedJobRegistry only holds
        # ids until result_ttl, which is enough to avoid clobbering a live run.
        for name in _REGISTRIES:
            registry = getattr(self._queue, name, None)
            getter = getattr(registry, "get_job_ids", None)
            if not callable(getter):
                continue
            try:
                ids = getter()
            except Exception:
                continue
            for rq_id in ids:
                add(rq_id)
        return ordered

    def _fetch(self, rq_job_id: str) -> object | None:
        fetch_job = getattr(self._queue, "fetch_job", None)
        if not callable(fetch_job):
            return None
        return fetch_job(rq_job_id)


def _job_status(job: object) -> str:
    getter = getattr(job, "get_status", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(refresh=False))
    except TypeError:
        return str(getter())
