from __future__ import annotations

import asyncio
import logging

from redis import Redis
from rq import Queue, Worker

from app.config.factory import (
    build_job_service,
    build_orchestrator,
)
from app.config.settings import Settings
from app.domain.errors import DomainError
from app.domain.jobs import is_terminal
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.rq_queue import RQ_QUEUE_NAME

logger = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    """Sync RQ entry: load job + source, run orchestrator, persist via JobStore."""
    asyncio.run(_process_job_async(job_id))


def boot() -> None:
    settings = Settings()
    asyncio.run(_recover_async(settings))
    _listen(settings)


async def _process_job_async(job_id: str) -> None:
    settings = Settings()
    filesystem = FilesystemJobStorage(settings.storage_path)
    orchestrator = build_orchestrator(settings)

    try:
        job = await filesystem.get_job(job_id)
    except DomainError:
        logger.error("worker_job_missing", extra={"job_id": job_id, "chunk_id": None})
        return
    if job is None:
        logger.error("worker_job_missing", extra={"job_id": job_id, "chunk_id": None})
        return
    if is_terminal(job.status):
        logger.info(
            "worker_job_skipped",
            extra={"job_id": job_id, "chunk_id": None, "status": job.status.value},
        )
        return

    text = await filesystem.read_source(job_id)
    workspace = settings.storage_path / "jobs" / job_id
    await orchestrator.run(job, text, workspace=workspace)


async def _recover_async(settings: Settings) -> list[str]:
    service = build_job_service(settings)
    return await service.recover_in_progress()


def _listen(settings: Settings) -> None:
    connection = Redis.from_url(str(settings.redis_url))
    queues = [Queue(RQ_QUEUE_NAME, connection=connection)]
    Worker(queues, connection=connection).work()
