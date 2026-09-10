from __future__ import annotations

import logging
from contextlib import suppress
from typing import Protocol

from redis.exceptions import RedisError

from app.domain.jobs import Job
from app.infrastructure.fs_storage import FilesystemJobStorage

logger = logging.getLogger(__name__)


class JobStatusCache(Protocol):
    async def save(self, job: Job) -> None: ...

    async def get(self, job_id: str) -> Job | None: ...

    async def delete(self, job_id: str) -> None: ...


class DualWriteJobStore:
    """Filesystem status.json is source of truth; Redis is a write-through GET hint."""

    def __init__(self, filesystem: FilesystemJobStorage, cache: JobStatusCache) -> None:
        self._filesystem = filesystem
        self._cache = cache

    async def save(self, job: Job) -> None:
        await self._filesystem.save_job(job)
        try:
            await self._cache.save(job)
        except Exception:
            logger.warning("job_cache_save_failed", extra={"job_id": job.id, "chunk_id": None})
            with suppress(Exception):
                await self._cache.delete(job.id)

    async def get(self, job_id: str) -> Job | None:
        try:
            cached = await self._cache.get(job_id)
        except RedisError:
            cached = None
        job = await self._filesystem.get_job(job_id)
        if job is not None:
            if cached != job:
                with suppress(Exception):
                    await self._cache.save(job)
            return job
        return cached

    async def delete(self, job_id: str) -> None:
        with suppress(Exception):
            await self._cache.delete(job_id)
        await self._filesystem.delete_job(job_id)

    async def list_ids(self) -> list[str]:
        return await self._filesystem.list_job_ids()
