"""RQ success hook: re-enqueue a job that yielded before its run budget ran out."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config.factory import build_job_service
from app.config.settings import Settings
from app.workers.rq_failure import _domain_job_id

logger = logging.getLogger(__name__)


def handle_rq_success(job: Any, connection: Any, result: Any) -> None:
    """RQ ``on_success`` callback: ``(job, connection, result)``."""
    del connection, result
    job_id = _domain_job_id(job)
    if not job_id:
        return
    try:
        asyncio.run(_continue(job_id))
    except Exception:
        logger.exception(
            "rq_continue_callback_failed",
            extra={"chunk_id": None, "job_id": job_id},
        )


async def _continue(job_id: str) -> None:
    settings = Settings()
    service = build_job_service(settings)
    await service.continue_if_requested(job_id)
