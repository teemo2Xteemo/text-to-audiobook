from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from shutil import copyfile, rmtree

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
from app.application.pipeline.merge_srt import merge_srt_cues
from app.application.pipeline.narrate import narrate_chunk
from app.application.pipeline.normalize import normalize_chunk
from app.application.pipeline.parse import is_srt_document, parse_source, prepare_narration_text
from app.application.pipeline.parse_srt import parse_srt_cues, serialize_srt_cues
from app.application.pipeline.retry import with_chunk_retry
from app.application.pipeline.timeline import schedule_srt_timeline
from app.application.pipeline.translate import translate_chunk
from app.application.pipeline.tts import select_voice, synthesize_chunk
from app.domain.audio import TTSSettings
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import (
    Job,
    JobMode,
    JobStatus,
    OutputFormat,
    assert_legal_transition,
    is_at_or_past,
    is_terminal,
)
from app.domain.languages import AUTO_SOURCE_LANGUAGE, resolve_source_language
from app.domain.ports import (
    AudioProcessor,
    CheckpointStorage,
    JobStore,
    LanguageDetector,
    NarrationProcessor,
    TranslationProvider,
    TTSProvider,
)
from app.domain.retry import RetryPolicy
from app.domain.srt import SRTCue, TimedChunk

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
        checkpoint_storage_factory: Callable[[Path], CheckpointStorage],
        artifact_cache: PipelineArtifactCache | None = None,
        max_chars: int = CHUNK_MAX_CHARS,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        translation_concurrency: int = 1,
        tts_concurrency: int = 1,
    ) -> None:
        self._translation = translation
        self._tts = tts
        self._narration = narration
        self._detector = detector
        self._audio = audio
        self._jobs = jobs
        self._artifact_cache = artifact_cache
        self._checkpoint_storage_factory = checkpoint_storage_factory
        self._max_chars = max_chars
        # TODO: require retry_policy (no constructor fallback). These literals
        # duplicate Settings / factory defaults; Settings should be the only source.
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=3, backoff_seconds=1.0)
        self._sleep = sleep or asyncio.sleep
        self._translation_concurrency = max(1, translation_concurrency)
        self._tts_concurrency = max(1, tts_concurrency)
        self._active_chunk_id: str | None = None

    async def run(self, job: Job, text: str, *, workspace: Path) -> Job:
        if is_terminal(job.status):
            return job
        if job.mode == JobMode.UNKNOWN:
            return await self._fail(job, ErrorType.INVALID_INPUT, "Unknown job mode")
        workspace.mkdir(parents=True, exist_ok=True)
        checkpoints = CheckpointStore(storage=self._checkpoint_storage_factory(workspace))
        if job.status is not JobStatus.QUEUED:
            logger.info(
                "pipeline_resumed",
                extra={"job_id": job.id, "chunk_id": None, "stage": job.status.value},
            )
        try:
            job = await self._advance(job, JobStatus.PARSING)
            if job.mode == JobMode.NARRATE:
                if is_srt_document(text):
                    raise DomainError(
                        ErrorType.INVALID_INPUT,
                        "SRT narrate mode is coming in phase 2",
                    )
                return await self._run_narrate(
                    job, text, workspace=workspace, checkpoints=checkpoints
                )

            if is_srt_document(text):
                return await self._run_srt(job, text, workspace=workspace, checkpoints=checkpoints)

            return await self._run_plain(job, text, workspace=workspace, checkpoints=checkpoints)
        except DomainError as exc:
            return await self._fail(job, exc.error_type, exc.message)
        except OSError:
            return await self._fail(job, ErrorType.STORAGE_FAILED, "failed to write artifacts")

    async def _run_plain(
        self,
        job: Job,
        text: str,
        *,
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        normalized, resolved = await parse_source(text, job.source_language, self._detector)
        chunks = chunk_source(normalized, max_chars=self._max_chars)
        job = replace(
            job,
            source_language=resolved,
            chunk_current=0 if job.status is JobStatus.PARSING else job.chunk_current,
            chunk_total=len(chunks),
        )
        await self._jobs.save(job)

        job = await self._advance(job, JobStatus.TRANSLATING)
        if job.source_language == job.target_language:
            job = await self._pass_through_chunks(
                job,
                chunks,
                workspace,
                checkpoints,
                stage=STAGE_TRANSLATED,
                active_status=JobStatus.TRANSLATING,
                suffix=".translated.txt",
            )
        else:
            job = await self._translate_chunks(job, chunks, workspace, checkpoints)

        return await self._finish_audio(job, chunks, workspace, checkpoints)

    async def _run_narrate(
        self,
        job: Job,
        text: str,
        *,
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        cleaned_text = prepare_narration_text(text)
        normalized, resolved = await parse_source(cleaned_text, job.source_language, self._detector)
        chunks = chunk_source(normalized, max_chars=self._max_chars)
        target_lang = (
            resolved
            if not job.target_language or job.target_language == AUTO_SOURCE_LANGUAGE
            else job.target_language
        )
        job = replace(
            job,
            source_language=resolved,
            target_language=target_lang,
            chunk_current=0 if job.status is JobStatus.PARSING else job.chunk_current,
            chunk_total=len(chunks),
        )
        await self._jobs.save(job)

        job = await self._advance(job, JobStatus.TRANSLATING)
        job = await self._pass_through_chunks(
            job,
            chunks,
            workspace,
            checkpoints,
            stage=STAGE_TRANSLATED,
            active_status=JobStatus.TRANSLATING,
            suffix=".translated.txt",
        )

        return await self._finish_audio(job, chunks, workspace, checkpoints)

    async def _finish_audio(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        job = await self._advance(job, JobStatus.PREPARING_TTS)
        job = await self._narrate_chunks(job, chunks, workspace, checkpoints)

        job = await self._advance(job, JobStatus.GENERATING_AUDIO)
        job = await self._generate_audio(job, chunks, workspace, checkpoints)

        job = await self._advance(job, JobStatus.MERGING)
        job = await self._merge(job, chunks, workspace)

        return await self._advance(job, JobStatus.COMPLETED)

    async def _run_srt(
        self,
        job: Job,
        text: str,
        *,
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        cues = parse_srt_cues(text)
        try:
            timed_chunks = merge_srt_cues(cues, max_chars=self._max_chars)
        except ValueError as exc:
            raise DomainError(ErrorType.INVALID_INPUT, f"invalid subtitle cues: {exc}") from exc
        if not timed_chunks:
            raise DomainError(ErrorType.INVALID_INPUT, "text is required")

        sample_text = " ".join(c.text for c in cues if c.text).strip()
        if not sample_text:
            raise DomainError(ErrorType.INVALID_INPUT, "text is required")

        resolved = await resolve_source_language(sample_text, job.source_language, self._detector)
        chunks = [
            Chunk(
                id=f"chunk-{tc.chunk_index + 1:03d}",
                text=tc.text,
                index=tc.chunk_index + 1,
            )
            for tc in timed_chunks
        ]
        job = replace(
            job,
            source_language=resolved,
            chunk_current=0 if job.status is JobStatus.PARSING else job.chunk_current,
            chunk_total=len(chunks),
        )
        await self._jobs.save(job)

        job = await self._advance(job, JobStatus.TRANSLATING)
        if job.source_language != job.target_language:
            job = await self._translate_chunks(job, chunks, workspace, checkpoints)
        else:
            job = await self._pass_through_chunks(
                job,
                chunks,
                workspace,
                checkpoints,
                stage=STAGE_TRANSLATED,
                active_status=JobStatus.TRANSLATING,
                suffix=".translated.txt",
            )

        job = await self._advance(job, JobStatus.PREPARING_TTS)
        job = await self._narrate_chunks(job, chunks, workspace, checkpoints)

        job = await self._advance(job, JobStatus.GENERATING_AUDIO)
        job = await self._generate_audio(job, chunks, workspace, checkpoints)

        job = await self._advance(job, JobStatus.MERGING)
        job = await self._merge_srt(job, timed_chunks, chunks, workspace)

        return await self._advance(job, JobStatus.COMPLETED)

    async def _pass_through_chunks(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
        *,
        stage: str,
        active_status: JobStatus,
        suffix: str,
    ) -> Job:
        job = await self._reset_chunk_progress(job, active_status)
        for chunk in chunks:
            self._active_chunk_id = chunk.id
            path = workspace / "chunks" / f"{chunk.id}{suffix}"
            if not checkpoints.is_complete(chunk.id, stage):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(chunk.text, encoding="utf-8")
                checkpoints.record(chunk.id, stage, path)
            job = await self._touch_chunk(job, chunk, stage, active_status=active_status)
        return job

    async def _merge_srt(
        self,
        job: Job,
        timed_chunks: list[TimedChunk],
        chunks: list[Chunk],
        workspace: Path,
    ) -> Job:
        ext = job.output_format.value
        translated_timed_chunks: list[TimedChunk] = []
        for i, tc in enumerate(timed_chunks):
            trans_path = workspace / "chunks" / f"{chunks[i].id}.translated.txt"
            if trans_path.is_file():
                chunk_text = trans_path.read_text(encoding="utf-8").strip()
            else:
                chunk_text = tc.text
            translated_timed_chunks.append(replace(tc, text=chunk_text))

        durations_ms: list[int] = []
        speech_wav_paths: list[Path] = []
        for chunk in chunks:
            speech_norm_path = workspace / "audio" / f"{chunk.id}.normalized.{ext}"
            speech_wav = workspace / "audio" / f"{chunk.id}.wav"
            if not _nonempty(speech_wav):
                await self._audio.normalize(
                    speech_norm_path,
                    speech_wav,
                    output_format=OutputFormat.WAV,
                    bitrate_kbps=job.output_bitrate_kbps,
                )
            dur = await self._audio.get_duration_ms(speech_wav)
            durations_ms.append(dur)
            speech_wav_paths.append(speech_wav)

        try:
            schedule = schedule_srt_timeline(translated_timed_chunks, durations_ms)
        except ValueError as exc:
            raise DomainError(
                ErrorType.AUDIO_PROCESSING_FAILED, f"timeline scheduling failed: {exc}"
            ) from exc

        silence_dir = workspace / "silence"
        silence_dir.mkdir(parents=True, exist_ok=True)
        merge_sources: list[Path] = []
        speech_durations: dict[int, int] = {}

        for idx, item in enumerate(schedule.items):
            if item.is_silence:
                silence_path = silence_dir / f"silence_{idx}.wav"
                await self._audio.generate_silence(
                    item.duration_ms,
                    silence_path,
                    output_format=OutputFormat.WAV,
                    bitrate_kbps=job.output_bitrate_kbps,
                )
                merge_sources.append(silence_path)
            else:
                assert item.chunk_index is not None
                chunk_id = chunks[item.chunk_index].id
                speech_wav = speech_wav_paths[item.chunk_index]
                if item.tempo_factor != 1.0:
                    tempo_path = workspace / "audio" / f"{chunk_id}.tempo.wav"
                    await self._audio.adjust_tempo(
                        speech_wav,
                        tempo_path,
                        item.tempo_factor,
                        output_format=OutputFormat.WAV,
                        bitrate_kbps=job.output_bitrate_kbps,
                    )
                    actual_dur = await self._audio.get_duration_ms(tempo_path)
                    speech_durations[item.chunk_index] = actual_dur
                    merge_sources.append(tempo_path)
                else:
                    speech_durations[item.chunk_index] = item.duration_ms
                    merge_sources.append(speech_wav)

        if not merge_sources:
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "audio merge failed")

        timeline_wav = workspace / "timeline.wav"
        await merge_artifacts(
            self._audio,
            merge_sources,
            timeline_wav,
            output_format=OutputFormat.WAV,
            bitrate_kbps=job.output_bitrate_kbps,
        )
        if not _nonempty(timeline_wav):
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "audio merge failed")

        destination = workspace / f"output.{ext}"
        if job.output_format is OutputFormat.MP3:
            # Re-encodes the merged timeline. The processor scales this call
            # with duration, up to the worker job timeout. Chunk calls stay short.
            await self._audio.normalize(
                timeline_wav,
                destination,
                output_format=OutputFormat.MP3,
                bitrate_kbps=job.output_bitrate_kbps,
                full_encode=True,
            )
        else:
            if destination != timeline_wav:
                copyfile(timeline_wav, destination)

        if not _nonempty(destination):
            raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "audio merge failed")

        timeline_wav.unlink(missing_ok=True)
        rmtree(silence_dir, ignore_errors=True)

        speech_items = [item for item in schedule.items if not item.is_silence]
        output_cues = [
            SRTCue(
                index=idx,
                start_ms=item.scheduled_start_ms,
                end_ms=item.scheduled_start_ms
                + speech_durations.get(item.chunk_index, item.duration_ms),
                text=item.subtitle_text or "",
            )
            for idx, item in enumerate(speech_items, start=1)
        ]
        output_srt_path = workspace / "output.srt"
        output_srt_path.write_text(serialize_srt_cues(output_cues), encoding="utf-8")

        logger.info(
            "pipeline_merged",
            extra={
                "job_id": job.id,
                "chunk_id": None,
                "stage": "merging",
                "max_drift_ms": schedule.max_drift_ms,
                "final_drift_ms": schedule.final_drift_ms,
            },
        )
        return job

    async def _translate_chunks(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        job = await self._reset_chunk_progress(job, JobStatus.TRANSLATING)
        lock = asyncio.Lock()
        already_completed_ids = {
            c.id for c in chunks if checkpoints.is_complete(c.id, STAGE_TRANSLATED)
        }
        completed_count = len(already_completed_ids)
        if completed_count > 0 and job.status is JobStatus.TRANSLATING:
            job = replace(job, chunk_current=completed_count)
            await self._jobs.save(job)

        async def _process_chunk(chunk: Chunk) -> None:
            nonlocal job, completed_count
            try:
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
                        async with lock:
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
                        async with lock:
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

                async with lock:
                    completed_count += 1
                    already_completed_ids.add(chunk.id)
                    if job.status is JobStatus.TRANSLATING:
                        job = replace(
                            job,
                            chunk_current=completed_count,
                        )
                        await self._jobs.save(job)
                    logger.info(
                        "pipeline_chunk_done",
                        extra={
                            "job_id": job.id,
                            "chunk_id": chunk.id,
                            "stage": STAGE_TRANSLATED,
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._active_chunk_id is None:
                    self._active_chunk_id = chunk.id
                raise

        queue: asyncio.Queue[Chunk] = asyncio.Queue()
        for chunk in chunks:
            if chunk.id not in already_completed_ids:
                queue.put_nowait(chunk)

        if queue.empty():
            return job

        self._active_chunk_id = None
        effective_concurrency = min(
            self._translation_concurrency,
            getattr(self._translation, "max_concurrency", self._translation_concurrency),
        )
        worker_count = min(effective_concurrency, queue.qsize())

        async def _worker() -> None:
            while not queue.empty():
                try:
                    chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await _process_chunk(chunk)
                queue.task_done()

        tasks = [asyncio.create_task(_worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return job

    async def _narrate_chunks(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        job = await self._reset_chunk_progress(job, JobStatus.PREPARING_TTS)
        already_completed_ids = {
            c.id for c in chunks if checkpoints.is_complete(c.id, STAGE_NARRATED)
        }
        completed_count = len(already_completed_ids)
        if completed_count > 0 and job.status is JobStatus.PREPARING_TTS:
            job = replace(job, chunk_current=completed_count)
            await self._jobs.save(job)

        for chunk in chunks:
            if chunk.id in already_completed_ids:
                continue
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
            completed_count += 1
            if job.status is JobStatus.PREPARING_TTS:
                job = replace(
                    job,
                    chunk_current=completed_count,
                )
                await self._jobs.save(job)
            logger.info(
                "pipeline_chunk_done",
                extra={"job_id": job.id, "chunk_id": chunk.id, "stage": STAGE_NARRATED},
            )
        return job

    async def _generate_audio(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        job = await self._reset_chunk_progress(job, JobStatus.GENERATING_AUDIO)
        ext = job.output_format.value
        settings = TTSSettings(speed=job.speed)
        lock = asyncio.Lock()
        already_completed_ids = {
            c.id
            for c in chunks
            if checkpoints.is_complete(c.id, STAGE_TTS)
            and checkpoints.is_complete(c.id, STAGE_NORMALIZED)
        }
        completed_count = len(already_completed_ids)
        if completed_count > 0 and job.status is JobStatus.GENERATING_AUDIO:
            job = replace(job, chunk_current=completed_count)
            await self._jobs.save(job)

        async def _process_chunk(chunk: Chunk) -> None:
            nonlocal job, completed_count
            try:
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
                        async with lock:
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
                                destination.unlink(missing_ok=True)
                                raise DomainError(ErrorType.TTS_FAILED, "tts failed")

                        await with_chunk_retry(
                            _synthesize,
                            policy=self._retry_policy,
                            sleep=self._sleep,
                            job_id=job.id,
                            chunk_id=chunk.id,
                            stage=STAGE_TTS,
                        )
                        async with lock:
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
                        raise DomainError(
                            ErrorType.AUDIO_PROCESSING_FAILED, "audio normalize failed"
                        )
                    async with lock:
                        checkpoints.record(chunk.id, STAGE_NORMALIZED, normalized_path)

                async with lock:
                    completed_count += 1
                    already_completed_ids.add(chunk.id)
                    if job.status is JobStatus.GENERATING_AUDIO:
                        job = replace(
                            job,
                            chunk_current=completed_count,
                        )
                        await self._jobs.save(job)
                    logger.info(
                        "pipeline_chunk_done",
                        extra={
                            "job_id": job.id,
                            "chunk_id": chunk.id,
                            "stage": "generating_audio",
                        },
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._active_chunk_id is None:
                    self._active_chunk_id = chunk.id
                raise

        queue: asyncio.Queue[Chunk] = asyncio.Queue()
        for chunk in chunks:
            if chunk.id not in already_completed_ids:
                queue.put_nowait(chunk)

        if queue.empty():
            return job

        self._active_chunk_id = None
        effective_concurrency = min(
            self._tts_concurrency,
            getattr(self._tts, "max_concurrency", self._tts_concurrency),
        )
        worker_count = min(effective_concurrency, queue.qsize())

        async def _worker() -> None:
            while not queue.empty():
                try:
                    chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await _process_chunk(chunk)
                queue.task_done()

        tasks = [asyncio.create_task(_worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
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
        reset_progress = status in {
            JobStatus.TRANSLATING,
            JobStatus.PREPARING_TTS,
            JobStatus.GENERATING_AUDIO,
        }
        job = replace(
            job,
            status=status,
            chunk_current=0 if reset_progress else job.chunk_current,
            recovery_attempts=0,
        )
        await self._jobs.save(job)
        return job

    async def _reset_chunk_progress(self, job: Job, active_status: JobStatus) -> Job:
        if job.status is not active_status or job.chunk_current > 0:
            return job
        job = replace(job, chunk_current=0)
        await self._jobs.save(job)
        return job

    async def _touch_chunk(
        self,
        job: Job,
        chunk: Chunk,
        stage: str,
        *,
        active_status: JobStatus,
    ) -> Job:
        if job.status is active_status:
            job = replace(
                job,
                chunk_current=max(job.chunk_current, chunk.index),
            )
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
        source.replace(destination)


def _nonempty(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0
