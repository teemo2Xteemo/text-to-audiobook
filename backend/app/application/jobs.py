from __future__ import annotations

import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass

from app.domain.audio import SPEED_DEFAULT, ensure_valid_speed
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.languages import ensure_valid_languages
from app.domain.ports import JobQueue, JobStore, SourceTextStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateJobCommand:
    source_language: str
    target_language: str
    text: str
    voice: str | None = None
    speed: float = SPEED_DEFAULT
    output_format: str = OutputFormat.MP3.value


class JobService:
    def __init__(
        self,
        *,
        jobs: JobStore,
        source_storage: SourceTextStorage,
        queue: JobQueue,
        output_bitrate_kbps: int,
    ) -> None:
        self._jobs = jobs
        self._source_storage = source_storage
        self._queue = queue
        self._output_bitrate_kbps = output_bitrate_kbps

    async def create(self, command: CreateJobCommand) -> Job:
        source_language = command.source_language.strip()
        target_language = command.target_language.strip()
        ensure_valid_languages(source_language, target_language)
        ensure_valid_speed(command.speed)
        text = command.text.strip()
        if not text:
            raise DomainError(ErrorType.INVALID_INPUT, "text is required")
        output_format = _parse_output_format(command.output_format)
        voice = command.voice.strip() if command.voice and command.voice.strip() else None

        job_id = str(uuid.uuid4())
        job = Job(
            id=job_id,
            status=JobStatus.QUEUED,
            source_language=source_language,
            target_language=target_language,
            voice=voice,
            speed=command.speed,
            output_format=output_format,
            output_bitrate_kbps=self._output_bitrate_kbps,
        )
        try:
            await self._source_storage.write_source(job_id, text)
            await self._jobs.save(job)
            await self._queue.enqueue(job_id)
        except DomainError:
            await self._cleanup(job_id)
            raise
        except Exception as exc:
            await self._cleanup(job_id)
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to persist job") from exc

        logger.info(
            "job_enqueued",
            extra={
                "job_id": job.id,
                "source_language": job.source_language,
                "status": job.status.value,
                "target_language": job.target_language,
            },
        )
        return job

    async def get(self, job_id: str) -> Job | None:
        return await self._jobs.get(job_id)

    async def _cleanup(self, job_id: str) -> None:
        with suppress(Exception):
            await self._source_storage.delete_job(job_id)
        with suppress(Exception):
            await self._jobs.delete(job_id)


def _parse_output_format(value: str) -> OutputFormat:
    try:
        return OutputFormat(value.strip().lower())
    except ValueError:
        raise DomainError(ErrorType.INVALID_INPUT, "output_format must be mp3 or wav") from None
