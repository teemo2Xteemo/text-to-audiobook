from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from app.application.jobs import CreateJobCommand, JobService
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore
from tests.fakes import FailingQueue, InMemoryJobStore, InMemoryQueue, InMemorySourceStorage


def _service(
    jobs: InMemoryJobStore | None = None,
    source: InMemorySourceStorage | None = None,
    queue: InMemoryQueue | FailingQueue | None = None,
) -> tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue | FailingQueue]:
    store = jobs or InMemoryJobStore()
    storage = source or InMemorySourceStorage()
    queued = queue or InMemoryQueue()
    service = JobService(
        jobs=store,
        source_storage=storage,
        queue=queued,
        output_bitrate_kbps=128,
        storage_path=Path("storage"),
    )
    return service, store, storage, queued


def test_create_job_persists_queued_and_enqueues_id_only() -> None:
    service, store, storage, queue = _service()
    job = asyncio.run(
        service.create(
            CreateJobCommand(
                source_language="ja-JP",
                target_language="en-US",
                text="  hello story  ",
                speed=1.25,
                output_format="wav",
            )
        )
    )
    assert job.status is JobStatus.QUEUED
    assert job.chunk_current == 0
    assert job.chunk_total == 0
    assert job.output_format is OutputFormat.WAV
    assert job.output_bitrate_kbps == 128
    assert job.speed == 1.25
    assert job.voice is None
    assert store.jobs[job.id] == job
    assert storage.texts[job.id] == "hello story"
    assert isinstance(queue, InMemoryQueue)
    assert queue.job_ids == [job.id]


def test_create_rejects_auto_target() -> None:
    service, _, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="zh-CN",
                    target_language="auto",
                    text="story",
                )
            )
        )
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_create_rejects_speed_out_of_range() -> None:
    service, _, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="en-US",
                    target_language="ja-JP",
                    text="story",
                    speed=2.01,
                )
            )
        )
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_create_rejects_unknown_output_format() -> None:
    service, _, _, _ = _service()
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="en-US",
                    target_language="ja-JP",
                    text="story",
                    output_format="flac",
                )
            )
        )
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_create_cleans_up_when_enqueue_fails() -> None:
    store = InMemoryJobStore()
    storage = InMemorySourceStorage()
    service, _, _, _ = _service(jobs=store, source=storage, queue=FailingQueue())
    with pytest.raises(DomainError) as exc:
        asyncio.run(
            service.create(
                CreateJobCommand(
                    source_language="en-US",
                    target_language="ko-KR",
                    text="story",
                )
            )
        )
    assert exc.value.error_type is ErrorType.STORAGE_FAILED
    assert store.jobs == {}
    assert storage.texts == {}


def test_get_returns_none_for_unknown_id() -> None:
    service, _, _, _ = _service()
    assert asyncio.run(service.get("missing")) is None


def test_retry_failed_job_requeues_same_id(tmp_path: Path) -> None:
    filesystem, cache, store, queue, service = _fs_retry_service(tmp_path)
    failed = replace(
        _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.FAILED),
        error_type=ErrorType.TTS_FAILED,
        message="tts failed",
        chunk_current=2,
        chunk_total=5,
    )
    asyncio.run(filesystem.save_job(failed))
    asyncio.run(filesystem.write_source(failed.id, "story to retry"))
    cache.jobs[failed.id] = failed

    retried = asyncio.run(service.retry(failed.id))
    assert retried.id == failed.id
    assert retried.status is JobStatus.QUEUED
    assert retried.error_type is None
    assert retried.message is None
    assert retried.source_language == "en-US"
    assert retried.target_language == "ko-KR"
    assert retried.chunk_current == 0
    assert retried.chunk_total == 0
    assert asyncio.run(filesystem.read_source(failed.id)) == "story to retry"
    assert queue.job_ids == [failed.id]
    assert asyncio.run(filesystem.get_job(failed.id)).status is JobStatus.QUEUED
    assert asyncio.run(store.get(failed.id)).status is JobStatus.QUEUED


def test_retry_completed_is_rejected(tmp_path: Path) -> None:
    filesystem, _, _, _, service = _fs_retry_service(tmp_path)
    completed = _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.COMPLETED)
    asyncio.run(filesystem.save_job(completed))
    with pytest.raises(DomainError) as exc:
        asyncio.run(service.retry(completed.id))
    assert exc.value.error_type is ErrorType.INVALID_INPUT
    assert exc.value.message == "job cannot be retried"


def test_retry_unknown_is_not_found(tmp_path: Path) -> None:
    _, _, _, _, service = _fs_retry_service(tmp_path)
    with pytest.raises(DomainError) as exc:
        asyncio.run(service.retry("11111111-1111-1111-1111-111111111111"))
    assert exc.value.error_type is ErrorType.INVALID_INPUT
    assert exc.value.message == "job not found"


def test_retry_reads_filesystem_status_not_job_store_cache(tmp_path: Path) -> None:
    filesystem, cache, store, queue, service = _fs_retry_service(tmp_path)
    failed = replace(
        _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.FAILED),
        error_type=ErrorType.TTS_FAILED,
        message="tts failed",
    )
    asyncio.run(filesystem.save_job(failed))
    cache.jobs[failed.id] = replace(failed, status=JobStatus.COMPLETED)
    assert asyncio.run(store.get(failed.id)).status is JobStatus.FAILED

    retried = asyncio.run(service.retry(failed.id))
    assert retried.status is JobStatus.QUEUED
    assert queue.job_ids == [failed.id]
    assert asyncio.run(filesystem.get_job(failed.id)).status is JobStatus.QUEUED


def test_recover_enqueues_non_terminal_only(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    queue = InMemoryQueue()
    service = JobService(
        jobs=InMemoryJobStore(),
        source_storage=InMemorySourceStorage(),
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
    )
    queued = _fs_job("11111111-1111-1111-1111-111111111111", JobStatus.QUEUED)
    running = _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.MERGING)
    failed = _fs_job("33333333-3333-3333-3333-333333333333", JobStatus.FAILED)
    completed = _fs_job("44444444-4444-4444-4444-444444444444", JobStatus.COMPLETED)
    asyncio.run(filesystem.save_job(queued))
    asyncio.run(filesystem.save_job(running))
    asyncio.run(filesystem.save_job(failed))
    asyncio.run(filesystem.save_job(completed))

    recovered = asyncio.run(service.recover_in_progress())
    assert set(recovered) == {queued.id, running.id}
    assert set(queue.job_ids) == {queued.id, running.id}


def test_recover_reads_filesystem_status_not_job_store_cache(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = _MemoryJobCache()
    store = DualWriteJobStore(filesystem, cache)
    queue = InMemoryQueue()
    service = JobService(
        jobs=store,
        source_storage=InMemorySourceStorage(),
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
    )
    running = _fs_job("22222222-2222-2222-2222-222222222222", JobStatus.GENERATING_AUDIO)
    asyncio.run(filesystem.save_job(running))
    cache.jobs[running.id] = replace(running, status=JobStatus.COMPLETED)
    assert asyncio.run(store.get(running.id)).status is JobStatus.GENERATING_AUDIO

    recovered = asyncio.run(service.recover_in_progress())
    assert recovered == [running.id]
    assert queue.job_ids == [running.id]


def test_mark_failed_from_non_terminal_updates_filesystem_and_cache(tmp_path: Path) -> None:
    filesystem, cache, store, queue, service = _fs_retry_service(tmp_path)
    running = _fs_job("66666666-6666-6666-6666-666666666666", JobStatus.GENERATING_AUDIO)
    asyncio.run(filesystem.save_job(running))
    cache.jobs[running.id] = replace(running, status=JobStatus.TRANSLATING)

    failed = asyncio.run(
        service.mark_failed(
            running.id, ErrorType.TIMEOUT, "job exceeded the worker timeout (1800s)"
        )
    )
    assert failed is not None
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.TIMEOUT
    assert failed.message == "job exceeded the worker timeout (1800s)"
    assert asyncio.run(filesystem.get_job(running.id)) == failed
    assert cache.jobs[running.id] == failed
    assert queue.job_ids == []


def test_mark_failed_is_noop_when_already_failed(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path)
    existing = replace(
        _fs_job("77777777-7777-7777-7777-777777777777", JobStatus.FAILED),
        error_type=ErrorType.TTS_FAILED,
        message="tts failed",
    )
    asyncio.run(filesystem.save_job(existing))
    cache.jobs[existing.id] = existing

    result = asyncio.run(service.mark_failed(existing.id, ErrorType.TIMEOUT, "timeout"))
    assert result == existing
    assert asyncio.run(filesystem.get_job(existing.id)) == existing
    assert cache.jobs[existing.id] == existing
    assert queue.job_ids == []


def test_mark_failed_is_noop_when_completed(tmp_path: Path) -> None:
    filesystem, _cache, _store, queue, service = _fs_retry_service(tmp_path)
    done = _fs_job("88888888-8888-8888-8888-888888888888", JobStatus.COMPLETED)
    asyncio.run(filesystem.save_job(done))

    result = asyncio.run(service.mark_failed(done.id, ErrorType.TIMEOUT, "timeout"))
    assert result is not None
    assert result.status is JobStatus.COMPLETED
    assert result.error_type is None
    assert asyncio.run(filesystem.get_job(done.id)) == done
    assert queue.job_ids == []


def test_mark_failed_missing_job_returns_none(tmp_path: Path) -> None:
    _filesystem, _cache, _store, queue, service = _fs_retry_service(tmp_path)
    result = asyncio.run(
        service.mark_failed("99999999-9999-9999-9999-999999999999", ErrorType.TIMEOUT, "timeout")
    )
    assert result is None
    assert queue.job_ids == []


def test_recover_does_not_enqueue_after_mark_failed(tmp_path: Path) -> None:
    filesystem, _cache, _store, queue, service = _fs_retry_service(tmp_path)
    running = _fs_job("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", JobStatus.TRANSLATING)
    asyncio.run(filesystem.save_job(running))
    asyncio.run(service.mark_failed(running.id, ErrorType.TIMEOUT, "timeout"))

    recovered = asyncio.run(service.recover_in_progress())
    assert recovered == []
    assert queue.job_ids == []


def test_get_returns_filesystem_failed_not_stale_non_terminal_cache(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path)
    running = _fs_job("dddddddd-dddd-dddd-dddd-dddddddddddd", JobStatus.TRANSLATING)
    failed = replace(
        running,
        status=JobStatus.FAILED,
        error_type=ErrorType.TIMEOUT,
        message="timeout",
    )
    asyncio.run(filesystem.save_job(failed))
    cache.jobs[running.id] = running
    assert asyncio.run(service.get(running.id)) == failed
    assert queue.job_ids == []


def test_get_returns_filesystem_queued_not_stale_failed_cache(tmp_path: Path) -> None:
    filesystem, cache, _store, queue, service = _fs_retry_service(tmp_path)
    queued = _fs_job("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee", JobStatus.QUEUED)
    failed = replace(
        queued,
        status=JobStatus.FAILED,
        error_type=ErrorType.TIMEOUT,
        message="timeout",
    )
    asyncio.run(filesystem.save_job(queued))
    cache.jobs[queued.id] = failed
    assert asyncio.run(service.get(queued.id)) == queued
    assert queue.job_ids == []


def _fs_retry_service(
    tmp_path: Path,
) -> tuple[FilesystemJobStorage, _MemoryJobCache, DualWriteJobStore, InMemoryQueue, JobService]:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = _MemoryJobCache()
    store = DualWriteJobStore(filesystem, cache)
    queue = InMemoryQueue()
    service = JobService(
        jobs=store,
        source_storage=filesystem,
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
    )
    return filesystem, cache, store, queue, service


def _fs_job(job_id: str, status: JobStatus) -> Job:
    return Job(
        id=job_id,
        status=status,
        source_language="en-US",
        target_language="ko-KR",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )


class _MemoryJobCache:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)
