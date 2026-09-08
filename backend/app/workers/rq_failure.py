"""RQ failure hooks: map worker kill/timeout onto the domain job FSM."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rq.timeouts import JobTimeoutException

from app.config.factory import build_job_service
from app.config.settings import Settings
from app.domain.errors import ErrorType

logger = logging.getLogger(__name__)


def handle_rq_failure(
    job: Any, connection: Any, exc_type: Any, exc_value: Any, traceback: Any
) -> None:
    """RQ ``on_failure`` callback: ``(job, connection, *exc_info)``."""
    del connection, traceback
    _apply(job, exc_type, exc_value)


def handle_worker_exception(job: Any, exc_type: Any, exc_value: Any, traceback: Any) -> None:
    """RQ ``exception_handlers`` omit the connection argument used by ``on_failure``."""
    handle_rq_failure(job, None, exc_type, exc_value, traceback)


def _apply(job: Any, exc_type: Any, exc_value: Any) -> None:
    job_id = _domain_job_id(job)
    if not job_id:
        logger.error("rq_failure_missing_job_id", extra={"job_id": None, "chunk_id": None})
        return
    try:
        asyncio.run(_mark(job_id, exc_type, exc_value))
    except Exception:
        logger.exception("rq_failure_callback_failed", extra={"job_id": job_id, "chunk_id": None})


async def _mark(job_id: str, exc_type: Any, exc_value: Any) -> None:
    settings = Settings()
    error_type, message = _map_error(exc_type, exc_value, settings.rq_job_timeout_seconds)
    service = build_job_service(settings)
    await service.mark_failed(job_id, error_type, message)


def _domain_job_id(job: Any) -> str | None:
    args = getattr(job, "args", None) or ()
    if args:
        raw = args[0]
        if raw:
            return str(raw)
    kwargs = getattr(job, "kwargs", None) or {}
    raw = kwargs.get("job_id")
    return str(raw) if raw else None


def _map_error(exc_type: Any, exc_value: Any, timeout_seconds: int) -> tuple[ErrorType, str]:
    if exc_type is JobTimeoutException or isinstance(exc_value, JobTimeoutException):
        return ErrorType.TIMEOUT, f"job exceeded the worker timeout ({timeout_seconds}s)"
    return ErrorType.STORAGE_FAILED, "worker task failed"
