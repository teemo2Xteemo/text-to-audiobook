from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.jobs import JobService
from app.config.settings import Settings
from app.domain.errors import ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore
from app.infrastructure.rq_queue import RQJobQueue
from app.workers.rq_continue import handle_rq_success
from tests.fakes import InMemoryQueue


class _MemoryJobCache:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


def _job(job_id: str, status: JobStatus) -> Job:
    return Job(
        id=job_id,
        status=status,
        source_language="en-US",
        target_language="ja-JP",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )


def _service(tmp_path: Path, queue: InMemoryQueue, *, max_job_runs: int = 500) -> JobService:
    filesystem = FilesystemJobStorage(tmp_path)
    return JobService(
        jobs=DualWriteJobStore(filesystem, _MemoryJobCache()),
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        max_job_runs=max_job_runs,
        max_stalled_runs=3,
    )


def test_success_enqueues_only_when_continuation_was_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    queued = replace(
        _job(job_id, JobStatus.GENERATING_AUDIO),
        continue_requested=True,
        chunk_current=10,
        chunk_total=40,
        progress_marker=10,
    )
    asyncio.run(FilesystemJobStorage(tmp_path).save_job(queued))
    queue = InMemoryQueue()
    service = _service(tmp_path, queue)
    monkeypatch.setattr(
        "app.workers.rq_continue.Settings",
        lambda **_: Settings(_env_file=None, storage_path=tmp_path),
    )
    monkeypatch.setattr("app.workers.rq_continue.build_job_service", lambda settings: service)

    handle_rq_success(SimpleNamespace(args=(job_id,)), None, None)

    continued = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job_id))
    assert continued is not None
    assert continued.status is JobStatus.GENERATING_AUDIO
    assert continued.continue_requested is False
    assert continued.run_count == 1
    assert continued.chunk_current == 10
    assert continued.chunk_total == 40
    assert queue.job_ids == [job_id]

    handle_rq_success(SimpleNamespace(args=(job_id,)), None, None)
    assert queue.job_ids == [job_id]


def test_timeout_counts_checkpoint_lines_the_status_file_has_not_caught_up(
    tmp_path: Path,
) -> None:
    job_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    workspace = tmp_path / "jobs" / job_id
    workspace.mkdir(parents=True)
    (workspace / "checkpoint.jsonl").write_text(
        '{"artifact_path":"chunks/a.txt","chunk_id":"chunk-001","stage":"tts"}\n',
        encoding="utf-8",
    )
    asyncio.run(
        FilesystemJobStorage(tmp_path).save_job(
            replace(
                _job(job_id, JobStatus.GENERATING_AUDIO),
                progress_marker=0,
                run_baseline_marker=0,
            )
        )
    )
    queue = InMemoryQueue()
    filesystem = FilesystemJobStorage(tmp_path)

    def read_marker(raw_job_id: str) -> int:
        text = (tmp_path / "jobs" / raw_job_id / "checkpoint.jsonl").read_text(encoding="utf-8")
        return sum(1 for line in text.splitlines() if line.strip())

    service = JobService(
        jobs=DualWriteJobStore(filesystem, _MemoryJobCache()),
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        progress_marker_reader=read_marker,
    )
    continued = asyncio.run(service.continue_after_timeout(job_id, timeout_seconds=1800))
    assert continued is not None
    assert continued.status is JobStatus.GENERATING_AUDIO
    assert continued.progress_marker == 1
    assert continued.recovery_attempts == 0
    assert queue.job_ids == [job_id]


def test_success_ignores_completed_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    asyncio.run(
        FilesystemJobStorage(tmp_path).save_job(
            replace(_job(job_id, JobStatus.COMPLETED), continue_requested=True)
        )
    )
    queue = InMemoryQueue()
    service = _service(tmp_path, queue)
    monkeypatch.setattr(
        "app.workers.rq_continue.Settings",
        lambda **_: Settings(_env_file=None, storage_path=tmp_path),
    )
    monkeypatch.setattr("app.workers.rq_continue.build_job_service", lambda settings: service)

    handle_rq_success(SimpleNamespace(args=(job_id,)), None, None)
    assert queue.job_ids == []


def test_success_fails_when_run_cap_is_already_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    asyncio.run(
        FilesystemJobStorage(tmp_path).save_job(
            replace(
                _job(job_id, JobStatus.TRANSLATING),
                continue_requested=True,
                run_count=2,
            )
        )
    )
    queue = InMemoryQueue()
    service = _service(tmp_path, queue, max_job_runs=2)
    monkeypatch.setattr(
        "app.workers.rq_continue.Settings",
        lambda **_: Settings(_env_file=None, storage_path=tmp_path),
    )
    monkeypatch.setattr("app.workers.rq_continue.build_job_service", lambda settings: service)

    handle_rq_success(SimpleNamespace(args=(job_id,)), None, None)
    failed = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job_id))
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.TIMEOUT
    assert failed.message == "job exceeded max runs (2)"
    assert queue.job_ids == []


def test_queued_rq_entry_is_not_duplicated_when_continuation_is_requested_twice(
    tmp_path: Path,
) -> None:
    job_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    class _Existing:
        def __init__(self, status: str) -> None:
            self.status = status

        def get_status(self, refresh: bool = True) -> str:
            del refresh
            return self.status

        def delete(self) -> None:
            self.status = "deleted"

    class _Dedup:
        def __init__(self) -> None:
            self.calls = 0
            self.existing: dict[str, _Existing] = {}

        def fetch_job(self, rq_job_id: str) -> _Existing | None:
            return self.existing.get(rq_job_id)

        def enqueue(self, *args: object, **kwargs: object) -> None:
            del args
            self.calls += 1
            self.existing[str(kwargs["job_id"])] = _Existing("queued")

    queue = _Dedup()
    rq_queue = RQJobQueue(queue, job_timeout=30)  # type: ignore[arg-type]
    filesystem = FilesystemJobStorage(tmp_path)
    service = JobService(
        jobs=DualWriteJobStore(filesystem, _MemoryJobCache()),
        source_storage=filesystem,
        queue=rq_queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
    )
    running = replace(_job(job_id, JobStatus.GENERATING_AUDIO), continue_requested=True)
    asyncio.run(filesystem.save_job(running))
    asyncio.run(service.continue_if_requested(job_id))
    asyncio.run(filesystem.save_job(replace(running, continue_requested=True, run_count=1)))
    asyncio.run(service.continue_if_requested(job_id))
    assert queue.calls == 1
