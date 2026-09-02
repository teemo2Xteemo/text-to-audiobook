from __future__ import annotations

import json

from redis import Redis
from redis.exceptions import RedisError

from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job

JOB_KEY_PREFIX = "job:"


class RedisJobCache:
    def __init__(self, client: Redis, *, key_prefix: str = JOB_KEY_PREFIX) -> None:
        self._client = client
        self._key_prefix = key_prefix

    async def save(self, job: Job) -> None:
        try:
            self._client.set(self._key(job.id), json.dumps(job.to_dict(), ensure_ascii=False))
        except RedisError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to cache job status") from exc

    async def get(self, job_id: str) -> Job | None:
        raw = self._client.get(self._key(job_id))
        if raw is None:
            return None
        try:
            payload = raw if isinstance(raw, str) else raw.decode("utf-8")
            return Job.from_dict(json.loads(payload))
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return None

    async def delete(self, job_id: str) -> None:
        try:
            self._client.delete(self._key(job_id))
        except RedisError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to delete job cache") from exc

    def _key(self, job_id: str) -> str:
        return f"{self._key_prefix}{job_id}"
