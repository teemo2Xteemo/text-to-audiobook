from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_job_service, get_settings
from app.api.errors import envelope
from app.api.parse import parse_create_job
from app.api.schemas import JobCreatedResponse, JobStatusResponse
from app.application.jobs import JobService
from app.config.settings import Settings
from app.domain.errors import ErrorType

router = APIRouter()


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
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=envelope(ErrorType.INVALID_INPUT, "job_id must be a UUID"),
        ) from None
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
        )
    return JobStatusResponse.from_job(job)
