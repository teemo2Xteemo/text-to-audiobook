import asyncio
from pathlib import Path

import pytest
from redis.exceptions import RedisError

from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.job_store import DualWriteJobStore


class MemoryCache:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


def _job(job_id: str = "11111111-1111-1111-1111-111111111111") -> Job:
    return Job(
        id=job_id,
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="ja-JP",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )


def test_filesystem_writes_source_and_status(tmp_path: Path) -> None:
    storage = FilesystemJobStorage(tmp_path)
    job = _job()
    asyncio.run(storage.write_source(job.id, "hello"))
    asyncio.run(storage.save_job(job))
    job_dir = tmp_path / "jobs" / job.id
    assert (job_dir / "source.txt").read_text(encoding="utf-8") == "hello"
    loaded = asyncio.run(storage.get_job(job.id))
    assert loaded == job
    assert asyncio.run(storage.read_source(job.id)) == "hello"


def test_filesystem_read_source_missing(tmp_path: Path) -> None:
    storage = FilesystemJobStorage(tmp_path)
    with pytest.raises(DomainError) as exc:
        asyncio.run(storage.read_source(_job().id))
    assert exc.value.error_type is ErrorType.STORAGE_FAILED


def test_dual_write_get_falls_back_to_filesystem(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = MemoryCache()
    store = DualWriteJobStore(filesystem, cache)
    job = _job()
    asyncio.run(store.save(job))
    cache.jobs.clear()
    loaded = asyncio.run(store.get(job.id))
    assert loaded == job
    assert cache.jobs[job.id] == job


class RedisErrorCache:
    def __init__(self) -> None:
        self.save_calls = 0

    async def save(self, job: Job) -> None:
        self.save_calls += 1
        raise RedisError("connection refused")

    async def get(self, job_id: str) -> Job | None:
        raise RedisError("connection refused")

    async def delete(self, job_id: str) -> None:
        raise RedisError("connection refused")


def test_dual_write_get_falls_back_when_redis_errors(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    job = _job()
    asyncio.run(filesystem.save_job(job))
    cache = RedisErrorCache()
    store = DualWriteJobStore(filesystem, cache)
    loaded = asyncio.run(store.get(job.id))
    assert loaded == job


def test_filesystem_list_job_ids_uuid_only(tmp_path: Path) -> None:
    storage = FilesystemJobStorage(tmp_path)
    job = _job()
    other = _job("22222222-2222-2222-2222-222222222222")
    asyncio.run(storage.save_job(job))
    asyncio.run(storage.save_job(other))
    stray = tmp_path / "jobs" / "not-a-uuid"
    stray.mkdir(parents=True)
    (stray / "status.json").write_text("{}", encoding="utf-8")
    empty = tmp_path / "jobs" / "33333333-3333-3333-3333-333333333333"
    empty.mkdir(parents=True)
    ids = asyncio.run(storage.list_job_ids())
    assert ids == [job.id, other.id]


def test_dual_write_list_ids_uses_filesystem(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = MemoryCache()
    store = DualWriteJobStore(filesystem, cache)
    job = _job()
    asyncio.run(store.save(job))
    assert asyncio.run(store.list_ids()) == [job.id]
