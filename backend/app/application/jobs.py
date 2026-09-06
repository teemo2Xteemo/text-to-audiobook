from __future__ import annotations

import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

from app.domain.audio import SPEED_DEFAULT, ensure_valid_speed
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat, assert_legal_transition, is_terminal
from app.domain.languages import ensure_valid_languages
from app.domain.ports import JobQueue, JobStore, SourceTextStorage
from app.infrastructure.fs_storage import FilesystemJobStorage

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
        storage_path: Path,
    ) -> None:
        self._jobs = jobs
        self._source_storage = source_storage
        self._queue = queue
        self._output_bitrate_kbps = output_bitrate_kbps
        self._storage_path = storage_path

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

    async def retry(self, job_id: str) -> Job:
        """Re-queue a FAILED job from filesystem ``status.json`` only.

        Do not use ``JobStore.get`` (Redis GET cache). HTTP 202/409 must follow
        the on-disk FSM even when the cache is stale or empty.
        """
        job = await self._filesystem().get_job(job_id)
        if job is None:
            raise DomainError(ErrorType.INVALID_INPUT, "job not found")
        if job.status is not JobStatus.FAILED:
            raise DomainError(ErrorType.INVALID_INPUT, "job cannot be retried")
        assert_legal_transition(job.status, JobStatus.QUEUED)
        job = replace(
            job,
            status=JobStatus.QUEUED,
            error_type=None,
            message=None,
            chunk_current=0,
            chunk_total=0,
        )
        await self._jobs.save(job)
        await self._queue.enqueue(job.id)
        logger.info(
            "job_retried",
            extra={
                "job_id": job.id,
                "source_language": job.source_language,
                "status": job.status.value,
                "target_language": job.target_language,
            },
        )
        return job

    async def recover_in_progress(self) -> list[str]:
        """Re-enqueue non-terminal jobs from filesystem ``status.json`` only.

        Do not use ``JobStore.get`` (Redis GET cache). Crash recovery must follow
        the on-disk FSM even when the cache is stale or empty.
        """
        filesystem = self._filesystem()
        recovered: list[str] = []
        for job_id in await filesystem.list_job_ids():
            try:
                job = await filesystem.get_job(job_id)
            except DomainError:
                logger.warning(
                    "job_recover_skipped",
                    extra={"job_id": job_id, "chunk_id": None},
                )
                continue
            if job is None or is_terminal(job.status):
                continue
            await self._queue.enqueue(job.id)
            recovered.append(job.id)
            logger.info(
                "job_recovered",
                extra={
                    "job_id": job.id,
                    "chunk_id": None,
                    "status": job.status.value,
                },
            )
        return recovered

    def output_audio_path(self, job: Job) -> Path:
        jobs_root = (self._storage_path / "jobs").resolve()
        directory = (jobs_root / job.id).resolve()
        if not directory.is_relative_to(jobs_root):
            raise DomainError(ErrorType.INVALID_INPUT, "invalid job_id")
        return directory / f"output.{job.output_format.value}"

    def _filesystem(self) -> FilesystemJobStorage:
        return FilesystemJobStorage(self._storage_path)

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
