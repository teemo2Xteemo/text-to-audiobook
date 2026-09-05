import asyncio
from pathlib import Path

import pytest

from app.application.jobs import CreateJobCommand, JobService
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import JobStatus, OutputFormat
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
