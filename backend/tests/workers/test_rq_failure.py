from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from rq.timeouts import JobTimeoutException

from app.application.jobs import JobService
from app.config.settings import Settings
from app.domain.errors import ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore
from app.workers.rq_failure import handle_rq_failure, handle_worker_exception
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


def _service(tmp_path: Path) -> JobService:
    filesystem = FilesystemJobStorage(tmp_path)
    return JobService(
        jobs=DualWriteJobStore(filesystem, _MemoryJobCache()),
        source_storage=filesystem,
        queue=InMemoryQueue(),
        output_bitrate_kbps=128,
        storage_path=tmp_path,
    )


def test_handle_rq_failure_timeout_marks_domain_job_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    asyncio.run(FilesystemJobStorage(tmp_path).save_job(_job(job_id, JobStatus.GENERATING_AUDIO)))
    service = _service(tmp_path)
    monkeypatch.setattr(
        "app.workers.rq_failure.Settings",
        lambda **_: Settings(_env_file=None, storage_path=tmp_path),
    )
    monkeypatch.setattr("app.workers.rq_failure.build_job_service", lambda settings: service)

    handle_rq_failure(
        SimpleNamespace(args=(job_id,)),
        None,
        JobTimeoutException,
        JobTimeoutException("Task exceeded maximum timeout value (180 seconds)"),
        None,
    )

    failed = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job_id))
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.TIMEOUT
    assert failed.message == "job exceeded the worker timeout (1800s)"


def test_handle_worker_exception_marks_unexpected_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    asyncio.run(FilesystemJobStorage(tmp_path).save_job(_job(job_id, JobStatus.TRANSLATING)))
    service = _service(tmp_path)
    monkeypatch.setattr(
        "app.workers.rq_failure.Settings",
        lambda **_: Settings(_env_file=None, storage_path=tmp_path),
    )
    monkeypatch.setattr("app.workers.rq_failure.build_job_service", lambda settings: service)

    handle_worker_exception(
        SimpleNamespace(args=(job_id,)), RuntimeError, RuntimeError("boom"), None
    )

    failed = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job_id))
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.STORAGE_FAILED
    assert failed.message == "worker task failed"
