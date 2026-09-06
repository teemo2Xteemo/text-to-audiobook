from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path

from app.application.pipeline.artifact_cache import PipelineArtifactCache
from app.application.pipeline.checkpoint import (
    STAGE_NARRATED,
    STAGE_NORMALIZED,
    STAGE_TRANSLATED,
    STAGE_TTS,
    CheckpointStore,
)
from app.application.pipeline.chunk import chunk_source
from app.application.pipeline.merge import merge_artifacts
from app.application.pipeline.narrate import narrate_chunk
from app.application.pipeline.normalize import normalize_chunk
from app.application.pipeline.parse import parse_source
from app.application.pipeline.retry import with_chunk_retry
from app.application.pipeline.translate import translate_chunk
from app.application.pipeline.tts import select_voice, synthesize_chunk
from app.domain.audio import TTSSettings
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, assert_legal_transition, is_at_or_past, is_terminal
from app.domain.ports import (
    AudioProcessor,
    JobStore,
    LanguageDetector,
    NarrationProcessor,
    TranslationProvider,
    TTSProvider,
)
from app.domain.retry import RetryPolicy

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        translation: TranslationProvider,
        tts: TTSProvider,
        narration: NarrationProcessor,
        detector: LanguageDetector,
        audio: AudioProcessor,
        jobs: JobStore,
        artifact_cache: PipelineArtifactCache | None = None,
        max_chars: int = CHUNK_MAX_CHARS,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._translation = translation
        self._tts = tts
        self._narration = narration
        self._detector = detector
        self._audio = audio
        self._jobs = jobs
        self._artifact_cache = artifact_cache
        self._max_chars = max_chars
        # TODO: require retry_policy (no constructor fallback). These literals
        # duplicate Settings / factory defaults; Settings should be the only source.
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=3, backoff_seconds=1.0)
        self._sleep = sleep or asyncio.sleep
        self._active_chunk_id: str | None = None

    async def run(self, job: Job, text: str, *, workspace: Path) -> Job:
        if is_terminal(job.status):
            return job
        workspace.mkdir(parents=True, exist_ok=True)
        checkpoints = CheckpointStore(workspace)
        if job.status is not JobStatus.QUEUED:
            logger.info(
                "pipeline_resumed",
                extra={"job_id": job.id, "chunk_id": None, "stage": job.status.value},
            )
        try:
            job = await self._advance(job, JobStatus.PARSING)
            normalized, resolved = await parse_source(text, job.source_language, self._detector)
            chunks = chunk_source(normalized, max_chars=self._max_chars)
            job = replace(
                job,
                source_language=resolved,
                chunk_current=0,
                chunk_total=len(chunks),
            )
            await self._jobs.save(job)

            job = await self._advance(job, JobStatus.TRANSLATING)
            job = await self._translate_chunks(job, chunks, workspace, checkpoints)

            job = await self._advance(job, JobStatus.PREPARING_TTS)
            job = await self._narrate_chunks(job, chunks, workspace, checkpoints)

            job = await self._advance(job, JobStatus.GENERATING_AUDIO)
            job = await self._generate_audio(job, chunks, workspace, checkpoints)

            job = await self._advance(job, JobStatus.MERGING)
            job = await self._merge(job, chunks, workspace)

            return await self._advance(job, JobStatus.COMPLETED)
        except DomainError as exc:
            return await self._fail(job, exc.error_type, exc.message)
        except OSError:
            return await self._fail(job, ErrorType.STORAGE_FAILED, "failed to write artifacts")

    async def _translate_chunks(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        job = await self._reset_chunk_progress(job)
        for chunk in chunks:
            self._active_chunk_id = chunk.id
            path = workspace / "chunks" / f"{chunk.id}.translated.txt"
            if not checkpoints.is_complete(chunk.id, STAGE_TRANSLATED):
                filled = False
                if self._artifact_cache is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    filled = self._artifact_cache.fill_translation(
                        text=chunk.text,
                        source_language=job.source_language,
                        target_language=job.target_language,
                        destination=path,
                        job_id=job.id,
                        chunk_id=chunk.id,
                    )
                if filled:
                    checkpoints.record(chunk.id, STAGE_TRANSLATED, path)
                else:

                    async def _translate(text: str = chunk.text) -> str:
                        return await translate_chunk(
                            text,
                            source_language=job.source_language,
                            target_language=job.target_language,
                            provider=self._translation,
                        )

                    translated = await with_chunk_retry(
                        _translate,
                        policy=self._retry_policy,
                        sleep=self._sleep,
                        job_id=job.id,
                        chunk_id=chunk.id,
                        stage=STAGE_TRANSLATED,
                    )
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(translated, encoding="utf-8")
                    checkpoints.record(chunk.id, STAGE_TRANSLATED, path)
                    if self._artifact_cache is not None:
                        self._artifact_cache.store_translation(
                            text=chunk.text,
                            source_language=job.source_language,
                            target_language=job.target_language,
                            source=path,
                            job_id=job.id,
                            chunk_id=chunk.id,
                        )
            job = await self._touch_chunk(job, chunk, STAGE_TRANSLATED)
        return job

    async def _narrate_chunks(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        job = await self._reset_chunk_progress(job)
        for chunk in chunks:
            self._active_chunk_id = chunk.id
            source = workspace / "chunks" / f"{chunk.id}.translated.txt"
            path = workspace / "chunks" / f"{chunk.id}.narrated.txt"
            if not checkpoints.is_complete(chunk.id, STAGE_NARRATED):
                narrated = narrate_chunk(
                    source.read_text(encoding="utf-8"),
                    language=job.target_language,
                    processor=self._narration,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(narrated, encoding="utf-8")
                checkpoints.record(chunk.id, STAGE_NARRATED, path)
            job = await self._touch_chunk(job, chunk, STAGE_NARRATED)
        return job

    async def _generate_audio(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        job = await self._reset_chunk_progress(job)
        ext = job.output_format.value
        settings = TTSSettings(speed=job.speed)
        for chunk in chunks:
            self._active_chunk_id = chunk.id
            narrated_path = workspace / "chunks" / f"{chunk.id}.narrated.txt"
            narrated = narrated_path.read_text(encoding="utf-8")
            raw_path = workspace / "audio" / f"{chunk.id}.{ext}"
            if not checkpoints.is_complete(chunk.id, STAGE_TTS):
                selected_voice = select_voice(self._tts, job.target_language, job.voice)
                filled = False
                if self._artifact_cache is not None:
                    filled = self._artifact_cache.fill_tts(
                        text=narrated,
                        source_language=job.source_language,
                        target_language=job.target_language,
                        voice=selected_voice,
                        settings=settings,
                        destination=raw_path,
                        job_id=job.id,
                        chunk_id=chunk.id,
                    )
                if filled:
                    checkpoints.record(chunk.id, STAGE_TTS, raw_path)
                else:

                    async def _synthesize(
                        narrated_text: str = narrated,
                        destination: Path = raw_path,
                        voice: str = selected_voice,
                    ) -> None:
                        artifact = await synthesize_chunk(
                            narrated_text,
                            language=job.target_language,
                            voice=voice,
                            settings=settings,
                            provider=self._tts,
                        )
                        _place_artifact(artifact.path, destination)
                        if not _nonempty(destination):
                            raise DomainError(ErrorType.TTS_FAILED, "tts failed")

                    await with_chunk_retry(
                        _synthesize,
                        policy=self._retry_policy,
                        sleep=self._sleep,
                        job_id=job.id,
                        chunk_id=chunk.id,
                        stage=STAGE_TTS,
                    )
                    checkpoints.record(chunk.id, STAGE_TTS, raw_path)
                    if self._artifact_cache is not None:
                        self._artifact_cache.store_tts(
                            text=narrated,
                            source_language=job.source_language,
                            target_language=job.target_language,
                            voice=selected_voice,
                            settings=settings,
                            source=raw_path,
                            job_id=job.id,
                            chunk_id=chunk.id,
                        )

            normalized_path = workspace / "audio" / f"{chunk.id}.normalized.{ext}"
            if not checkpoints.is_complete(chunk.id, STAGE_NORMALIZED):
                await normalize_chunk(
                    self._audio,
                    raw_path,
                    normalized_path,
                    output_format=job.output_format,
                    bitrate_kbps=job.output_bitrate_kbps,
                )
                if not _nonempty(normalized_path):
                    raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "audio normalize failed")
                checkpoints.record(chunk.id, STAGE_NORMALIZED, normalized_path)
            job = await self._touch_chunk(job, chunk, "generating_audio")
        return job

    async def _merge(self, job: Job, chunks: list[Chunk], workspace: Path) -> Job:
        ext = job.output_format.value
        sources = [workspace / "audio" / f"{chunk.id}.normalized.{ext}" for chunk in chunks]
        destination = workspace / f"output.{ext}"
        await merge_artifacts(
            self._audio,
            sources,
            destination,
            output_format=job.output_format,
            bitrate_kbps=job.output_bitrate_kbps,
        )
        if not _nonempty(destination):
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "audio merge failed")
        logger.info(
            "pipeline_merged",
            extra={"job_id": job.id, "chunk_id": None, "stage": "merging"},
        )
        return job

    async def _advance(self, job: Job, status: JobStatus) -> Job:
        if is_at_or_past(job.status, status):
            return job
        assert_legal_transition(job.status, status)
        job = replace(job, status=status)
        await self._jobs.save(job)
        return job

    async def _reset_chunk_progress(self, job: Job) -> Job:
        job = replace(job, chunk_current=0)
        await self._jobs.save(job)
        return job

    async def _touch_chunk(self, job: Job, chunk: Chunk, stage: str) -> Job:
        job = replace(job, chunk_current=chunk.index)
        await self._jobs.save(job)
        logger.info(
            "pipeline_chunk_done",
            extra={"job_id": job.id, "chunk_id": chunk.id, "stage": stage},
        )
        return job

    async def _fail(self, job: Job, error_type: ErrorType, message: str) -> Job:
        stage = job.status.value
        if job.status is not JobStatus.FAILED:
            assert_legal_transition(job.status, JobStatus.FAILED)
            job = replace(job, status=JobStatus.FAILED, error_type=error_type, message=message)
            await self._jobs.save(job)
        logger.info(
            "pipeline_failed",
            extra={
                "error_type": error_type.value,
                "job_id": job.id,
                "chunk_id": self._active_chunk_id,
                "stage": stage,
            },
        )
        return job


def _place_artifact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0
