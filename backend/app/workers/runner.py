from __future__ import annotations

import asyncio
import logging

from app.config.factory import build_job_store, build_orchestrator, build_source_storage
from app.config.settings import Settings

logger = logging.getLogger(__name__)


def process_job(job_id: str) -> None:
    """Sync RQ entry: load job + source, run orchestrator, persist via JobStore."""
    asyncio.run(_process_job_async(job_id))


async def _process_job_async(job_id: str) -> None:
    settings = Settings()
    store = build_job_store(settings)
    source_storage = build_source_storage(settings)
    orchestrator = build_orchestrator(settings)

    job = await store.get(job_id)
    if job is None:
        logger.error("worker_job_missing", extra={"job_id": job_id, "chunk_id": None})
        return

    text = await source_storage.read_source(job_id)
    workspace = settings.storage_path / "jobs" / job_id
    await orchestrator.run(job, text, workspace=workspace)
