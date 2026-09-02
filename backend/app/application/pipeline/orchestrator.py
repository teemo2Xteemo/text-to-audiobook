from __future__ import annotations

import logging
import shutil
from dataclasses import replace
from pathlib import Path

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
from app.application.pipeline.translate import translate_chunk
from app.application.pipeline.tts import synthesize_chunk
from app.domain.audio import TTSSettings
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, assert_legal_transition
from app.domain.ports import (
    AudioProcessor,
    JobStore,
    LanguageDetector,
    NarrationProcessor,
    TranslationProvider,
    TTSProvider,
)

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
        max_chars: int = CHUNK_MAX_CHARS,
    ) -> None:
        self._translation = translation
        self._tts = tts
        self._narration = narration
        self._detector = detector
        self._audio = audio
        self._jobs = jobs
        self._max_chars = max_chars

    async def run(self, job: Job, text: str, *, workspace: Path) -> Job:
        workspace.mkdir(parents=True, exist_ok=True)
        checkpoints = CheckpointStore(workspace)
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
            path = workspace / "chunks" / f"{chunk.id}.translated.txt"
            if not checkpoints.is_complete(chunk.id, STAGE_TRANSLATED):
                translated = await translate_chunk(
                    chunk.text,
                    source_language=job.source_language,
                    target_language=job.target_language,
                    provider=self._translation,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(translated, encoding="utf-8")
                checkpoints.record(chunk.id, STAGE_TRANSLATED, path)
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
            narrated_path = workspace / "chunks" / f"{chunk.id}.narrated.txt"
            narrated = narrated_path.read_text(encoding="utf-8")
            raw_path = workspace / "audio" / f"{chunk.id}.{ext}"
            if not checkpoints.is_complete(chunk.id, STAGE_TTS):
                artifact = await synthesize_chunk(
                    narrated,
                    language=job.target_language,
                    voice=job.voice,
                    settings=settings,
                    provider=self._tts,
                )
                _place_artifact(artifact.path, raw_path)
                if not _nonempty(raw_path):
                    raise DomainError(ErrorType.TTS_FAILED, "tts failed")
                checkpoints.record(chunk.id, STAGE_TTS, raw_path)

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
