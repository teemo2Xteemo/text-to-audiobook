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
