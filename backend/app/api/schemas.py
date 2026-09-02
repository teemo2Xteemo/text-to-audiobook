from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.audio import SPEED_DEFAULT
from app.domain.jobs import Job, JobStatus, OutputFormat


class ErrorEnvelope(BaseModel):
    error_type: str
    message: str


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    stage: JobStatus
    chunk_current: int
    chunk_total: int
    error_type: str | None
    message: str | None
    source_language: str
    target_language: str
    voice: str | None
    speed: float
    output_format: OutputFormat

    @classmethod
    def from_job(cls, job: Job) -> JobStatusResponse:
        return cls(
            job_id=job.id,
            status=job.status,
            stage=job.status,
            chunk_current=job.chunk_current,
            chunk_total=job.chunk_total,
            error_type=job.error_type.value if job.error_type is not None else None,
            message=job.message,
            source_language=job.source_language,
            target_language=job.target_language,
            voice=job.voice,
            speed=job.speed,
            output_format=job.output_format,
        )


class CreateJobJsonBody(BaseModel):
    text: str
    source_language: str
    target_language: str
    voice: str | None = None
    speed: float = SPEED_DEFAULT
    output_format: str = Field(default=OutputFormat.MP3.value)
