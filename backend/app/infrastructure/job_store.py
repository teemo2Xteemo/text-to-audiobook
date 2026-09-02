from __future__ import annotations

from contextlib import suppress
from typing import Protocol

from redis.exceptions import RedisError

from app.domain.jobs import Job
from app.infrastructure.fs_storage import FilesystemJobStorage


class JobStatusCache(Protocol):
    async def save(self, job: Job) -> None: ...

    async def get(self, job_id: str) -> Job | None: ...

    async def delete(self, job_id: str) -> None: ...


class DualWriteJobStore:
    """Filesystem status.json is source of truth; Redis is a GET cache."""

    def __init__(self, filesystem: FilesystemJobStorage, cache: JobStatusCache) -> None:
        self._filesystem = filesystem
        self._cache = cache

    async def save(self, job: Job) -> None:
        await self._filesystem.save_job(job)
        await self._cache.save(job)

    async def get(self, job_id: str) -> Job | None:
        try:
            cached = await self._cache.get(job_id)
        except RedisError:
            cached = None
        if cached is not None:
            return cached
        job = await self._filesystem.get_job(job_id)
        if job is not None:
            with suppress(Exception):
                await self._cache.save(job)
        return job

    async def delete(self, job_id: str) -> None:
        with suppress(Exception):
            await self._cache.delete(job_id)
        await self._filesystem.delete_job(job_id)
