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
    assert service._queue.job_ids == []  # type: ignore[attr-defined]


def test_timeout_with_checkpoint_progress_reenqueues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    job_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    running = replace(
        _job(job_id, JobStatus.GENERATING_AUDIO),
        progress_marker=12,
        run_baseline_marker=4,
        recovery_attempts=2,
        stalled_runs=2,
    )
    asyncio.run(FilesystemJobStorage(tmp_path).save_job(running))
    queue = InMemoryQueue()
    filesystem = FilesystemJobStorage(tmp_path)
    service = JobService(
        jobs=DualWriteJobStore(filesystem, _MemoryJobCache()),
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        max_job_runs=500,
    )
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

    continued = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job_id))
    assert continued is not None
    assert continued.status is JobStatus.GENERATING_AUDIO
    assert continued.error_type is None
    assert continued.recovery_attempts == 0
    assert continued.stalled_runs == 0
    assert continued.run_count == 1
    assert continued.run_baseline_marker == 12
    assert queue.job_ids == [job_id]


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
    assert failed.error_type is ErrorType.WORKER_FAILED
    assert failed.message == "worker task failed (RuntimeError)"


def test_handle_rq_failure_provider_configuration_error_maps_correctly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config.factory import ProviderConfigurationError

    job_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    asyncio.run(FilesystemJobStorage(tmp_path).save_job(_job(job_id, JobStatus.QUEUED)))
    service = _service(tmp_path)
    monkeypatch.setattr(
        "app.workers.rq_failure.Settings",
        lambda **_: Settings(_env_file=None, storage_path=tmp_path),
    )
    monkeypatch.setattr("app.workers.rq_failure.build_job_service", lambda settings: service)

    handle_rq_failure(
        SimpleNamespace(args=(job_id,)),
        None,
        ProviderConfigurationError,
        ProviderConfigurationError("GEMINI_API_KEY is required when TRANSLATION_PROVIDER=gemini"),
        None,
    )

    failed = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job_id))
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.PROVIDER_CONFIGURATION_ERROR
    assert "GEMINI_API_KEY" in (failed.message or "")


def test_handle_rq_failure_domain_error_preserves_error_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain.errors import DomainError

    job_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    asyncio.run(FilesystemJobStorage(tmp_path).save_job(_job(job_id, JobStatus.TRANSLATING)))
    service = _service(tmp_path)
    monkeypatch.setattr(
        "app.workers.rq_failure.Settings",
        lambda **_: Settings(_env_file=None, storage_path=tmp_path),
    )
    monkeypatch.setattr("app.workers.rq_failure.build_job_service", lambda settings: service)

    handle_rq_failure(
        SimpleNamespace(args=(job_id,)),
        None,
        DomainError,
        DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "source language not supported"),
        None,
    )

    failed = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job_id))
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.UNSUPPORTED_LANGUAGE
    assert failed.message == "source language not supported"
