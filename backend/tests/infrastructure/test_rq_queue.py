import asyncio
from importlib import import_module

from rq.job import Callback

from app.infrastructure.rq_queue import (
    RQ_ON_FAILURE,
    RQ_PROCESS_JOB,
    RQJobQueue,
)


class _CaptureQueue:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}
        self.args: tuple[object, ...] = ()

    def enqueue(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs


def test_enqueue_sets_timeout_and_on_failure() -> None:
    queue = _CaptureQueue()
    asyncio.run(RQJobQueue(queue, job_timeout=1800).enqueue("11111111-1111-1111-1111-111111111111"))
    assert queue.args[0] == RQ_PROCESS_JOB
    assert queue.kwargs["job_timeout"] == 1800
    on_failure = queue.kwargs["on_failure"]
    assert isinstance(on_failure, Callback)
    assert on_failure.name == RQ_ON_FAILURE


def test_rq_on_failure_path_resolves() -> None:
    module_path, name = RQ_ON_FAILURE.rsplit(".", 1)
    assert callable(getattr(import_module(module_path), name))


def test_enqueue_runs_in_worker_thread() -> None:
    import threading

    main_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    class _ThreadQueue(_CaptureQueue):
        def enqueue(self, *args: object, **kwargs: object) -> None:
            seen.append(threading.current_thread())
            super().enqueue(*args, **kwargs)

    queue = _ThreadQueue()
    asyncio.run(RQJobQueue(queue, job_timeout=1800).enqueue("11111111-1111-1111-1111-111111111111"))  # type: ignore[arg-type]
    assert len(seen) == 1
    assert seen[0] is not main_thread


def test_enqueue_uses_deterministic_job_id_and_skips_when_already_queued() -> None:
    job_id = "11111111-1111-1111-1111-111111111111"

    class _ExistingJob:
        def __init__(self, status: str) -> None:
            self._status = status

        def get_status(self, refresh: bool = True) -> str:
            del refresh
            return self._status

        def delete(self) -> None:
            return None

    class _DedupQueue(_CaptureQueue):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0
            self.existing: dict[str, _ExistingJob] = {}

        def fetch_job(self, rq_job_id: str) -> _ExistingJob | None:
            return self.existing.get(rq_job_id)

        def enqueue(self, *args: object, **kwargs: object) -> None:
            self.calls += 1
            super().enqueue(*args, **kwargs)
            rq_id = str(kwargs["job_id"])
            self.existing[rq_id] = _ExistingJob("queued")

    queue = _DedupQueue()
    rq_queue = RQJobQueue(queue, job_timeout=1800)  # type: ignore[arg-type]
    asyncio.run(rq_queue.enqueue(job_id))
    asyncio.run(rq_queue.enqueue(job_id))
    assert queue.calls == 1
    assert queue.kwargs["job_id"] == f"process-{job_id}"

    # When the previous RQ job is failed/finished, re-enqueueing is allowed (e.g. retry)
    queue.existing[f"process-{job_id}"] = _ExistingJob("failed")
    asyncio.run(rq_queue.enqueue(job_id))
    assert queue.calls == 2


def test_enqueue_replaces_stale_started_entry_after_crash() -> None:
    job_id = "11111111-1111-1111-1111-111111111111"
    rq_id = f"process-{job_id}"
    deleted: list[str] = []

    class _StartedJob:
        def get_status(self, refresh: bool = True) -> str:
            del refresh
            return "started"

        def delete(self) -> None:
            deleted.append(rq_id)

    class _CrashedQueue(_CaptureQueue):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def fetch_job(self, rq_job_id: str) -> _StartedJob | None:
            return _StartedJob() if rq_job_id == rq_id else None

        def enqueue(self, *args: object, **kwargs: object) -> None:
            self.calls += 1
            super().enqueue(*args, **kwargs)

    queue = _CrashedQueue()
    asyncio.run(RQJobQueue(queue, job_timeout=1800).enqueue(job_id))  # type: ignore[arg-type]
    assert deleted == [rq_id]
    assert queue.calls == 1
    assert queue.kwargs["job_id"] == rq_id


def test_rq_queue_enqueue_cleans_started_job_registry_with_fakeredis() -> None:
    import fakeredis
    from rq import Queue
    from rq.executions import Execution

    client = fakeredis.FakeStrictRedis()
    queue = Queue("test-recovery-queue", connection=client)
    job_id = "11111111-1111-1111-1111-111111111111"
    rq_job_id = f"process-{job_id}"

    job = queue.enqueue(RQ_PROCESS_JOB, job_id, job_id=rq_job_id)
    with client.pipeline() as pipe:
        job.prepare_for_execution("worker-test", pipe)
        Execution.create(job, ttl=300, pipeline=pipe, worker_name="worker-test")
        pipe.execute()

    job.refresh()
    assert job.get_status() == "started"
    assert job.id in queue.started_job_registry

    rq_queue = RQJobQueue(queue, job_timeout=1800)
    asyncio.run(rq_queue.enqueue(job_id))

    assert job.id not in queue.started_job_registry
    assert rq_job_id in queue.job_ids
    new_job = queue.fetch_job(rq_job_id)
    assert new_job is not None
    assert new_job.get_status() == "queued"
