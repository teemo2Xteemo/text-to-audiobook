from __future__ import annotations

import logging
import uuid
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from app.domain.audio import SPEED_DEFAULT, ensure_valid_speed
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat, assert_legal_transition, is_terminal
from app.domain.languages import ensure_valid_languages
from app.domain.ports import DiskJobStore, JobQueue, JobStore, SourceTextStorage

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
        disk_jobs: DiskJobStore | None = None,
        max_recovery_attempts: int = 3,
        translation_provider: str = "fake",
        translation_model: str = "fake",
    ) -> None:
        self._jobs = jobs
        self._source_storage = source_storage
        self._queue = queue
        self._output_bitrate_kbps = output_bitrate_kbps
        self._storage_path = storage_path
        self._disk_jobs: DiskJobStore = (
            disk_jobs if disk_jobs is not None else _JobStoreDiskAdapter(jobs)
        )
        self._max_recovery_attempts = max_recovery_attempts
        self._translation_provider = translation_provider
        self._translation_model = translation_model

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
            translation_provider=self._translation_provider,
            translation_model=self._translation_model,
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
                "translation_model": job.translation_model,
                "translation_provider": job.translation_provider,
            },
        )
        return job

    async def get(self, job_id: str) -> Job | None:
        return await self._jobs.get(job_id)

    async def mark_failed(self, job_id: str, error_type: ErrorType, message: str) -> Job | None:
        """Mark a non-terminal job FAILED from filesystem ``status.json``.

        RQ timeout-kill never reaches ``PipelineOrchestrator._fail``. Without this
        hop, GET polling stays on a non-terminal status (or boot recover silently
        re-enqueues). No-op when the job is missing or already terminal.
        """
        job = await self._filesystem().get_job(job_id)
        if job is None:
            logger.warning("job_fail_missing", extra={"job_id": job_id, "chunk_id": None})
            return None
        if is_terminal(job.status):
            logger.info(
                "job_fail_skipped",
                extra={"job_id": job.id, "chunk_id": None, "status": job.status.value},
            )
            return job
        assert_legal_transition(job.status, JobStatus.FAILED)
        job = replace(
            job,
            status=JobStatus.FAILED,
            error_type=error_type,
            message=message,
        )
        await self._jobs.save(job)
        logger.info(
            "job_failed",
            extra={
                "chunk_id": None,
                "error_type": error_type.value,
                "job_id": job.id,
                "status": job.status.value,
            },
        )
        return job

    async def retry(self, job_id: str) -> Job:
        """Re-queue a FAILED job from filesystem ``status.json`` only.

        DualWrite.get prefers filesystem; retry still reads ``status.json``
        directly so HTTP 202/409 follow the on-disk FSM without cache refresh.
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
            recovery_attempts=0,
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

        DualWrite.get prefers filesystem; recover still reads ``status.json``
        directly so crash recovery follows the on-disk FSM without cache refresh.
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
            if self._is_job_locked(job.id):
                logger.info(
                    "job_recover_skipped_locked",
                    extra={"job_id": job.id, "chunk_id": None},
                )
                continue
            if job.status is JobStatus.QUEUED:
                updated = job
            else:
                if job.recovery_attempts >= self._max_recovery_attempts:
                    await self.mark_failed(
                        job.id,
                        ErrorType.WORKER_FAILED,
                        "job exceeded max crash recovery attempts",
                    )
                    continue
                updated = replace(job, recovery_attempts=job.recovery_attempts + 1)
            await self._jobs.save(updated)
            await self._queue.enqueue(job.id)
            recovered.append(job.id)
            logger.info(
                "job_recovered",
                extra={
                    "job_id": job.id,
                    "chunk_id": None,
                    "recovery_attempts": updated.recovery_attempts,
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

    def output_subtitles_path(self, job: Job) -> Path:
        jobs_root = (self._storage_path / "jobs").resolve()
        directory = (jobs_root / job.id).resolve()
        if not directory.is_relative_to(jobs_root):
            raise DomainError(ErrorType.INVALID_INPUT, "invalid job_id")
        return directory / "output.srt"

    def get_subtitles_path(self, job_id: str) -> Path:
        jobs_root = (self._storage_path / "jobs").resolve()
        directory = (jobs_root / job_id).resolve()
        if not directory.is_relative_to(jobs_root):
            raise DomainError(ErrorType.INVALID_INPUT, "invalid job_id")
        return directory / "output.srt"

    def _filesystem(self) -> DiskJobStore:
        return self._disk_jobs

    async def _cleanup(self, job_id: str) -> None:
        with suppress(Exception):
            await self._source_storage.delete_job(job_id)
        with suppress(Exception):
            await self._jobs.delete(job_id)

    def _is_job_locked(self, job_id: str) -> bool:
        if fcntl is None:
            return False
        jobs_root = (self._storage_path / "jobs").resolve()
        lock_path = (jobs_root / job_id / ".lock").resolve()
        if not lock_path.is_relative_to(jobs_root):
            return False
        if not lock_path.is_file():
            return False
        try:
            with lock_path.open("r", encoding="utf-8") as lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    return False
                except OSError:
                    return True
        except OSError:
            return False


class _JobStoreDiskAdapter:
    def __init__(self, jobs: JobStore) -> None:
        self._jobs = jobs

    async def save_job(self, job: Job) -> None:
        await self._jobs.save(job)

    async def get_job(self, job_id: str) -> Job | None:
        return await self._jobs.get(job_id)

    async def list_job_ids(self) -> list[str]:
        return list(await self._jobs.list_ids())


def _parse_output_format(value: str) -> OutputFormat:
    try:
        return OutputFormat(value.strip().lower())
    except ValueError:
        raise DomainError(ErrorType.INVALID_INPUT, "output_format must be mp3 or wav") from None
