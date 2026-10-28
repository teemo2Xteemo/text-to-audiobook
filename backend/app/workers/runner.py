from __future__ import annotations

import asyncio
import fcntl
import logging
from multiprocessing import Process

from redis import Redis
from rq import Queue, Worker

from app.config.factory import (
    ProviderConfigurationError,
    build_job_service,
    build_orchestrator,
    settings_for_translation_pin,
)
from app.config.settings import Settings
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import JobMode, is_terminal
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.rq_queue import RQ_QUEUE_NAME
from app.workers.rq_failure import handle_worker_exception

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

    workspace = settings.storage_path / "jobs" / job_id
    workspace.mkdir(parents=True, exist_ok=True)
    lock_path = workspace / ".lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.info("worker_job_locked", extra={"job_id": job_id, "chunk_id": None})
            return
        try:
            # Re-read under the lock: another worker may have finished this job since
            # the pre-lock snapshot above was taken.
            fresh = await filesystem.get_job(job_id)
            if fresh is None or is_terminal(fresh.status):
                logger.info(
                    "worker_job_skipped",
                    extra={
                        "job_id": job_id,
                        "chunk_id": None,
                        "status": fresh.status.value if fresh is not None else "missing",
                    },
                )
                return
            if fresh.mode == JobMode.UNKNOWN:
                logger.error(
                    "worker_job_unknown_mode",
                    extra={"job_id": job_id},
                )
                service = build_job_service(settings)
                await service.mark_failed(
                    job_id,
                    ErrorType.INVALID_INPUT,
                    "Unknown job mode",
                )
                return

            text = await filesystem.read_source(job_id)
            try:
                orchestrator = build_orchestrator(
                    settings_for_translation_pin(settings, fresh),
                    for_mode=fresh.mode,
                )
            except ProviderConfigurationError as exc:
                logger.error(
                    "worker_provider_configuration_error",
                    extra={"job_id": job_id, "error": str(exc)},
                )
                service = build_job_service(settings)
                await service.mark_failed(
                    job_id,
                    ErrorType.PROVIDER_CONFIGURATION_ERROR,
                    str(exc),
                )
                return
            await orchestrator.run(fresh, text, workspace=workspace)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def _recover_async(settings: Settings) -> list[str]:
    service = build_job_service(settings)
    return await service.recover_in_progress()


def _run_worker(settings: Settings) -> None:
    connection = Redis.from_url(str(settings.redis_url))
    timeout = settings.rq_job_timeout_seconds
    queues = [Queue(RQ_QUEUE_NAME, connection=connection, default_timeout=timeout)]
    Worker(
        queues,
        connection=connection,
        exception_handlers=[handle_worker_exception],
    ).work()


def _listen(settings: Settings) -> None:
    # Note: on Python >= 3.14, Linux defaults multiprocessing to 'forkserver',
    # so Settings must remain picklable. For production multi-worker supervision,
    # scaling independent containers (`docker compose up --scale worker=N`) is preferred.
    children: list[Process] = []
    for _ in range(max(0, settings.worker_concurrency - 1)):
        proc = Process(target=_run_worker, args=(settings,), daemon=True)
        proc.start()
        children.append(proc)
    try:
        _run_worker(settings)
    finally:
        for proc in children:
            if proc.is_alive():
                proc.terminate()
            proc.join(timeout=5)
