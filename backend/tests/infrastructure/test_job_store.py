import asyncio
from dataclasses import replace
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


def test_dual_write_get_prefers_filesystem_queued_over_stale_failed_cache(
    tmp_path: Path,
) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = MemoryCache()
    store = DualWriteJobStore(filesystem, cache)
    queued = _job()
    failed = replace(queued, status=JobStatus.FAILED, error_type=ErrorType.TIMEOUT)
    asyncio.run(filesystem.save_job(queued))
    cache.jobs[queued.id] = failed

    loaded = asyncio.run(store.get(queued.id))
    assert loaded == queued
    assert loaded is not None
    assert loaded.status is JobStatus.QUEUED
    assert cache.jobs[queued.id] == queued


def test_dual_write_get_prefers_filesystem_when_terminal(
    tmp_path: Path,
) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = MemoryCache()
    store = DualWriteJobStore(filesystem, cache)
    running = _job()
    failed = replace(running, status=JobStatus.FAILED, error_type=ErrorType.TIMEOUT)
    asyncio.run(filesystem.save_job(failed))
    cache.jobs[running.id] = running

    loaded = asyncio.run(store.get(running.id))
    assert loaded == failed
    assert loaded is not None
    assert loaded.status is JobStatus.FAILED
    assert cache.jobs[running.id] == failed


def test_dual_write_save_invalidates_cache_when_cache_write_fails(tmp_path: Path) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    running = _job()
    cache = _FailingSaveCache(running)
    store = DualWriteJobStore(filesystem, cache)
    failed = replace(running, status=JobStatus.FAILED, error_type=ErrorType.TIMEOUT)
    asyncio.run(store.save(failed))
    assert asyncio.run(filesystem.get_job(running.id)) == failed
    assert running.id not in cache.jobs
    loaded = asyncio.run(store.get(running.id))
    assert loaded == failed
    assert loaded is not None
    assert loaded.status is JobStatus.FAILED


class _FailingSaveCache:
    def __init__(self, job: Job) -> None:
        self.jobs: dict[str, Job] = {job.id: job}

    async def save(self, job: Job) -> None:
        raise RedisError("readonly replica")

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


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


def test_dual_write_get_returns_none_and_evicts_orphan_cache_when_filesystem_missing(
    tmp_path: Path,
) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    cache = MemoryCache()
    store = DualWriteJobStore(filesystem, cache)
    orphan = _job()
    cache.jobs[orphan.id] = orphan

    loaded = asyncio.run(store.get(orphan.id))
    assert loaded is None
    assert orphan.id not in cache.jobs


def test_filesystem_save_job_preserves_previous_status_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = FilesystemJobStorage(tmp_path)
    initial = _job()
    asyncio.run(storage.save_job(initial))
    job_dir = tmp_path / "jobs" / initial.id

    def _boom(self: Path, target: Path) -> Path:
        raise OSError("simulated disk error during replace")

    monkeypatch.setattr(Path, "replace", _boom)
    updated = replace(initial, status=JobStatus.TRANSLATING, chunk_current=1, chunk_total=3)
    with pytest.raises(DomainError) as exc:
        asyncio.run(storage.save_job(updated))
    assert exc.value.error_type is ErrorType.STORAGE_FAILED
    assert asyncio.run(storage.get_job(initial.id)) == initial
    assert list(job_dir.glob("*.tmp")) == []


def test_filesystem_write_source_cleans_up_tmp_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = FilesystemJobStorage(tmp_path)
    job = _job()
    asyncio.run(storage.write_source(job.id, "original story"))
    job_dir = tmp_path / "jobs" / job.id

    def _boom(self: Path, target: Path) -> Path:
        raise OSError("simulated disk error during replace")

    monkeypatch.setattr(Path, "replace", _boom)
    with pytest.raises(DomainError) as exc:
        asyncio.run(storage.write_source(job.id, "corrupted overwrite"))
    assert exc.value.error_type is ErrorType.STORAGE_FAILED
    assert asyncio.run(storage.read_source(job.id)) == "original story"
    assert list(job_dir.glob("*.tmp")) == []


def test_redis_job_cache_runs_sync_client_in_worker_thread() -> None:
    import threading

    from app.infrastructure.redis_job_store import RedisJobCache

    main_thread = threading.current_thread()
    seen_threads: list[threading.Thread] = []
    store_data: dict[str, str] = {}

    class _FakeRedis:
        def set(self, key: str, value: str) -> None:
            seen_threads.append(threading.current_thread())
            store_data[key] = value

        def get(self, key: str) -> str | None:
            seen_threads.append(threading.current_thread())
            return store_data.get(key)

        def delete(self, key: str) -> None:
            seen_threads.append(threading.current_thread())
            store_data.pop(key, None)

    cache = RedisJobCache(_FakeRedis())  # type: ignore[arg-type]
    job = _job()
    asyncio.run(cache.save(job))
    assert asyncio.run(cache.get(job.id)) == job
    asyncio.run(cache.delete(job.id))
    assert len(seen_threads) == 3
    assert all(thread is not main_thread for thread in seen_threads)
