from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.deps import get_job_service, get_settings
from app.api.errors import envelope
from app.api.parse import parse_create_job
from app.api.schemas import JobCreatedResponse, JobStatusResponse
from app.application.jobs import JobService
from app.config.settings import Settings
from app.domain.errors import ErrorType
from app.domain.jobs import JobStatus, OutputFormat

router = APIRouter()

_MEDIA_TYPES = {
    OutputFormat.MP3: "audio/mpeg",
    OutputFormat.WAV: "audio/wav",
}


@router.post("/api/jobs", status_code=202, response_model=JobCreatedResponse)
async def create_job(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: JobService = Depends(get_job_service),
) -> JobCreatedResponse:
    command = await parse_create_job(request, settings)
    job = await service.create(command)
    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobStatusResponse:
    _require_uuid(job_id)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
        )
    return JobStatusResponse.from_job(job)


@router.get("/api/jobs/{job_id}/audio")
async def download_job_audio(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> FileResponse:
    _require_uuid(job_id)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
        )
    if job.status is not JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.INVALID_INPUT, "audio not ready"),
        )
    path = service.output_audio_path(job)
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.STORAGE_FAILED, "audio artifact missing"),
        )
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[job.output_format],
        filename=f"{job.id}.{job.output_format.value}",
    )


def _require_uuid(job_id: str) -> None:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=envelope(ErrorType.INVALID_INPUT, "job_id must be a UUID"),
        ) from None
