from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from app.application.pipeline.parse import is_srt_document, strip_srt
from app.application.pipeline.tts import resolve_voice
from app.domain.audio import SPEED_DEFAULT, ensure_valid_speed
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import (
    Job,
    JobMode,
    JobStatus,
    OutputFormat,
    SrtTiming,
    assert_legal_transition,
    is_terminal,
)
from app.domain.languages import AUTO_SOURCE_LANGUAGE, ensure_valid_languages
from app.domain.ports import (
    DiskJobStore,
    JobQueue,
    JobStore,
    LanguageDetector,
    SourceTextStorage,
    TTSProvider,
)
from app.domain.translation_profile import TranslationProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateJobCommand:
    source_language: str
    target_language: str
    text: str
    voice: str | None = None
    speed: float = SPEED_DEFAULT
    output_format: str = OutputFormat.MP3.value
    translation_profile_id: str | None = None
    mode: JobMode = JobMode.TRANSLATE
    srt_timing: SrtTiming = SrtTiming.CONTINUOUS


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
        available_profiles: Sequence[TranslationProfile] | None = None,
        tts: TTSProvider | None = None,
        detector: LanguageDetector | None = None,
        language_confirm_threshold: float = 0.70,
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
        self._available_profiles = (
            list(available_profiles) if available_profiles is not None else []
        )
        self._tts = tts
        self._detector = detector
        self._language_confirm_threshold = language_confirm_threshold

    async def create(self, command: CreateJobCommand) -> Job:
        source_language = command.source_language.strip()
        target_language = command.target_language.strip()
        ensure_valid_speed(command.speed)
        text = command.text.strip()
        if not text:
            raise DomainError(ErrorType.INVALID_INPUT, "text is required")
        output_format = _parse_output_format(command.output_format)
        voice = command.voice.strip() if command.voice and command.voice.strip() else None

        if command.mode == JobMode.NARRATE:
            if (
                command.translation_profile_id is not None
                and command.translation_profile_id.strip()
            ):
                raise DomainError(
                    ErrorType.INVALID_INPUT,
                    "translation profile not permitted in narrate mode",
                )

            if not source_language or source_language == AUTO_SOURCE_LANGUAGE:
                if self._detector is not None:
                    sample = strip_srt(text[:8192]) if is_srt_document(text[:8192]) else text[:8192]
                    detection = await self._detector.detect(sample[:8192])
                    if detection.confidence < self._language_confirm_threshold:
                        msg = (
                            f"Language detection confidence ({detection.confidence:.2f}) "
                            f"is below threshold ({self._language_confirm_threshold:.2f}); "
                            "please specify language explicitly"
                        )
                        raise DomainError(ErrorType.INVALID_INPUT, msg)
                    resolved_source = detection.language_code
                else:
                    raise DomainError(
                        ErrorType.INVALID_INPUT,
                        "auto language detection unavailable",
                    )
            else:
                resolved_source = source_language

            if (
                target_language
                and target_language != AUTO_SOURCE_LANGUAGE
                and target_language != resolved_source
            ):
                raise DomainError(
                    ErrorType.INVALID_INPUT,
                    "target_language must match source_language in narrate mode",
                )

            resolved_target = resolved_source

            if self._tts is not None:
                resolved_voice = resolve_voice(self._tts, resolved_source, voice)
            else:
                resolved_voice = voice

            translation_provider = None
            translation_model = None
            source_language = resolved_source
            target_language = resolved_target
            voice = resolved_voice
        else:
            if command.srt_timing is not SrtTiming.CONTINUOUS:
                # Translate mode always voices subtitles on their cue timeline.
                raise DomainError(
                    ErrorType.INVALID_INPUT, "srt_timing is only supported in narrate mode"
                )
            ensure_valid_languages(source_language, target_language)

            profile_id = (
                command.translation_profile_id.strip()
                if command.translation_profile_id and command.translation_profile_id.strip()
                else None
            )
            matched = None
            if profile_id is not None:
                matched = next(
                    (p for p in self._available_profiles if p.id == profile_id),
                    None,
                )
                if matched is None:
                    trunc_id = profile_id[:64]
                    raise DomainError(
                        ErrorType.INVALID_INPUT,
                        f"unknown translation_profile_id: {trunc_id}",
                    )
                translation_provider = matched.provider
                translation_model = matched.model
            else:
                translation_provider = self._translation_provider
                translation_model = self._translation_model
                matched = next(
                    (p for p in self._available_profiles if p.is_default),
                    None,
                )

            if matched is not None and matched.supported_languages:
                if (
                    source_language != AUTO_SOURCE_LANGUAGE
                    and source_language not in matched.supported_languages
                ):
                    raise DomainError(
                        ErrorType.UNSUPPORTED_LANGUAGE,
                        (
                            f"source language '{source_language}' is not supported "
                            f"by translation profile '{matched.id}'"
                        ),
                    )
                if target_language not in matched.supported_languages:
                    raise DomainError(
                        ErrorType.UNSUPPORTED_LANGUAGE,
                        (
                            f"target language '{target_language}' is not supported "
                            f"by translation profile '{matched.id}'"
                        ),
                    )

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
            translation_provider=translation_provider,
            translation_model=translation_model,
            mode=command.mode,
            srt_timing=command.srt_timing,
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
                "mode": job.mode.value,
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
