from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from shutil import copyfile, rmtree

from app.application.capabilities import refresh_engine_voices
from app.application.pipeline.artifact_cache import PipelineArtifactCache
from app.application.pipeline.chapter_assembly import (
    assemble_ready_chapters,
    invalidate_chapters_for_chunks,
)
from app.application.pipeline.chapters import (
    build_chapter_manifest,
    plan_chapter_chunks,
    split_chapters,
)
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
from app.application.pipeline.narrate_plan import (
    load_narrate_plan,
    plan_narration_chunks,
    prepared_narration_text,
    save_narrate_plan,
)
from app.application.pipeline.normalize import normalize_chunk
from app.application.pipeline.parse import is_srt_document, parse_source
from app.application.pipeline.parse_srt import parse_srt_cues, serialize_srt_cues
from app.application.pipeline.retry import with_chunk_retry
from app.application.pipeline.speech_normalization import normalization_problem
from app.application.pipeline.timeline import schedule_srt_timeline
from app.application.pipeline.translate import translate_chunk
from app.application.pipeline.tts import select_voice, synthesize_chunk
from app.domain.audio import TTSSettings
from app.domain.chapters import ChapterManifest
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import (
    Job,
    JobMode,
    JobStatus,
    OutputFormat,
    SrtTiming,
    assert_legal_transition,
    is_at_or_past,
    is_terminal,
)
from app.domain.languages import AUTO_SOURCE_LANGUAGE, resolve_source_language
from app.domain.ports import (
    AudioProcessor,
    ChapterManifestStore,
    CheckpointStorage,
    JobStore,
    LanguageDetector,
    NarrationProcessor,
    TextNormalizer,
    TranslationProvider,
    TTSProvider,
)
from app.domain.retry import RetryPolicy
from app.domain.srt import SRTCue, TimedChunk

logger = logging.getLogger(__name__)


def _legacy_chunk_progress(workspace: Path, checkpoints: CheckpointStore) -> bool:
    """In-flight work from before chapter manifests. Do not re-cut it."""
    if checkpoints.entry_count() > 0:
        return True
    chunks_dir = workspace / "chunks"
    if not chunks_dir.is_dir():
        return False
    return any(chunks_dir.glob("chunk-*.translated.txt"))


def _forget_chunks_whose_text_changed(
    workspace: Path,
    checkpoints: CheckpointStore,
    chunks: list[Chunk],
) -> list[int]:
    """A checkpoint is for one text. A new split must not reuse its audio.

    Returns chunk indexes whose checkpoints were dropped. Their chapter files
    are stale and must be invalidated by the caller.
    """
    forgotten: list[int] = []
    for chunk in chunks:
        path = workspace / "chunks" / f"{chunk.id}.translated.txt"
        if not path.is_file():
            continue
        try:
            saved = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if saved != chunk.text:
            checkpoints.forget_chunk(chunk.id)
            forgotten.append(chunk.index)
    return forgotten


class _BudgetYield(Exception):
    """Stop scheduling new work so the worker can enqueue another run."""

    def __init__(self, job: Job) -> None:
        self.job = job
        super().__init__(job.id)


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
        chapter_manifest_factory: Callable[[Path], ChapterManifestStore] | None = None,
        artifact_cache: PipelineArtifactCache | None = None,
        max_chars: int = CHUNK_MAX_CHARS,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        translation_concurrency: int = 1,
        tts_concurrency: int = 1,
        chapter_pause_ms: int = 0,
        text_normalizer: TextNormalizer | None = None,
        max_stalled_runs: int = 3,
        max_job_runs: int = 500,
    ) -> None:
        self._translation = translation
        self._tts = tts
        self._narration = narration
        self._detector = detector
        self._audio = audio
        self._jobs = jobs
        self._artifact_cache = artifact_cache
        self._checkpoint_storage_factory = checkpoint_storage_factory
        self._chapter_manifest_factory = chapter_manifest_factory
        self._chapter_lock = asyncio.Lock()
        self._manifest_bound = False
        self._manifest: ChapterManifest | None = None
        self._manifest_store: ChapterManifestStore | None = None
        self._max_chars = max_chars
        # TODO: require retry_policy (no constructor fallback). These literals
        # duplicate Settings / factory defaults; Settings should be the only source.
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=3, backoff_seconds=1.0)
        self._sleep = sleep or asyncio.sleep
        self._translation_concurrency = max(1, translation_concurrency)
        self._tts_concurrency = max(1, tts_concurrency)
        self._chapter_pause_ms = max(0, chapter_pause_ms)
        self._text_normalizer = text_normalizer
        self._active_chunk_id: str | None = None
        self._max_stalled_runs = max(1, max_stalled_runs)
        self._max_job_runs = max(1, max_job_runs)
        self._deadline: float | None = None
        self._budget_window: float | None = None
        self._run_baseline = 0
        self._checkpoints: CheckpointStore | None = None

    async def run(
        self,
        job: Job,
        text: str,
        *,
        workspace: Path,
        deadline: float | None = None,
    ) -> Job:
        if is_terminal(job.status):
            return job
        if job.mode == JobMode.UNKNOWN:
            return await self._fail(job, ErrorType.INVALID_INPUT, "Unknown job mode")
        workspace.mkdir(parents=True, exist_ok=True)
        self._manifest_bound = False
        self._manifest = None
        self._manifest_store = None
        checkpoints = CheckpointStore(storage=self._checkpoint_storage_factory(workspace))
        self._checkpoints = checkpoints
        self._deadline = deadline
        started = time.monotonic()
        self._budget_window = None if deadline is None else max(0.0, deadline - started)
        if job.status is not JobStatus.QUEUED:
            logger.info(
                "pipeline_resumed",
                extra={"job_id": job.id, "chunk_id": None, "stage": job.status.value},
            )
        try:
            checkpoints.compact()
            self._run_baseline = checkpoints.entry_count()
            if job.run_baseline_marker != self._run_baseline:
                job = replace(job, run_baseline_marker=self._run_baseline)
                await self._jobs.save(job)
            job = await self._advance(job, JobStatus.PARSING)
            if job.mode == JobMode.NARRATE and not (
                job.srt_timing is SrtTiming.ALIGNED and is_srt_document(text)
            ):
                result = await self._run_narrate(
                    job, text, workspace=workspace, checkpoints=checkpoints
                )
            elif is_srt_document(text):
                result = await self._run_srt(
                    job, text, workspace=workspace, checkpoints=checkpoints
                )
            else:
                result = await self._run_plain(
                    job, text, workspace=workspace, checkpoints=checkpoints
                )
        except _BudgetYield as yielded:
            return await self._yield(yielded.job, checkpoints)
        except DomainError as exc:
            return await self._fail(job, exc.error_type, exc.message)
        except OSError:
            return await self._fail(job, ErrorType.STORAGE_FAILED, "failed to write artifacts")
        return await self._commit_progress(result, checkpoints)

    async def _run_plain(
        self,
        job: Job,
        text: str,
        *,
        workspace: Path,
        checkpoints: CheckpointStore,
    ) -> Job:
        normalized, resolved = await parse_source(text, job.source_language, self._detector)
        chunks = self._chunks_for(
            normalized,
            workspace,
            checkpoints,
            forget_on_plan=False,
            legacy_if_in_flight=True,
        )
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
        _, resolved = await parse_source(text, job.source_language, self._detector)
        chunks = self._chunks_for(
            text,
            workspace,
            checkpoints,
            forget_on_plan=True,
            legacy_if_in_flight=False,
            narration=True,
        )
        if not chunks:
            raise DomainError(ErrorType.INVALID_INPUT, "No readable text found for narration")
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

        job = await self._begin_merge(job, len(chunks))
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
        # Narrate never translates, even if source/target tags drift apart after resolution.
        if job.mode is not JobMode.NARRATE and job.source_language != job.target_language:
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

        job = await self._begin_merge(job, len(chunks))
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
                if self._budget_exhausted():
                    raise _BudgetYield(job)
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
                        job = self._with_chunk_progress(job, chunk_current=completed_count)
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
        await self._drain_chunk_queue(
            queue,
            _process_chunk,
            worker_count=min(effective_concurrency, queue.qsize()),
        )
        if self._budget_exhausted() and not queue.empty():
            raise _BudgetYield(job)
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

        pending = [chunk for chunk in chunks if chunk.id not in already_completed_ids]
        spoken_by_id = (
            await self._normalize_pending(job, pending, workspace) if job.normalize_text else {}
        )
        for chunk in pending:
            if job.normalize_text and chunk.id not in spoken_by_id:
                # In-flight normalizations already finished and are narrated below.
                # Do not start another LLM call once the budget is spent.
                raise _BudgetYield(job)
            if not job.normalize_text and self._budget_exhausted():
                raise _BudgetYield(job)
            self._active_chunk_id = chunk.id
            source = workspace / "chunks" / f"{chunk.id}.translated.txt"
            path = workspace / "chunks" / f"{chunk.id}.narrated.txt"
            if not checkpoints.is_complete(chunk.id, STAGE_NARRATED):
                spoken = spoken_by_id.get(chunk.id) or source.read_text(encoding="utf-8")
                narrated = narrate_chunk(
                    spoken,
                    language=job.target_language,
                    processor=self._narration,
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(narrated, encoding="utf-8")
                checkpoints.record(chunk.id, STAGE_NARRATED, path)
            completed_count += 1
            if job.status is JobStatus.PREPARING_TTS:
                job = self._with_chunk_progress(job, chunk_current=completed_count)
                await self._jobs.save(job)
            logger.info(
                "pipeline_chunk_done",
                extra={"job_id": job.id, "chunk_id": chunk.id, "stage": STAGE_NARRATED},
            )
        return job

    async def _normalize_pending(
        self, job: Job, chunks: list[Chunk], workspace: Path
    ) -> dict[str, str]:
        """Normalize chunks with bounded concurrency; narration itself stays sequential.

        Workers stop taking new chunks once the run budget is spent. Tasks already
        started are allowed to finish so their output can be checkpointed.
        """
        if not chunks:
            return {}
        limit = min(
            self._translation_concurrency,
            getattr(self._text_normalizer, "max_concurrency", self._translation_concurrency),
        )
        spoken_by_id: dict[str, str] = {}
        lock = asyncio.Lock()
        queue: asyncio.Queue[Chunk] = asyncio.Queue()
        for chunk in chunks:
            queue.put_nowait(chunk)

        async def _one(chunk: Chunk) -> None:
            source = workspace / "chunks" / f"{chunk.id}.translated.txt"
            text = source.read_text(encoding="utf-8")
            try:
                spoken = await self._normalize_for_speech(job, chunk, text, workspace)
                async with lock:
                    spoken_by_id[chunk.id] = spoken
                    done = len(spoken_by_id)
                logger.info(
                    "pipeline_chunk_normalized",
                    extra={
                        "job_id": job.id,
                        "chunk_id": chunk.id,
                        "done": done,
                        "total": len(chunks),
                    },
                )
            except Exception:
                if self._active_chunk_id is None:
                    self._active_chunk_id = chunk.id
                raise

        async def _worker() -> None:
            while True:
                if self._budget_exhausted():
                    return
                try:
                    chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await _one(chunk)

        self._active_chunk_id = None
        worker_count = min(max(limit, 1), queue.qsize())
        tasks = [asyncio.create_task(_worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return spoken_by_id

    async def _normalize_for_speech(
        self, job: Job, chunk: Chunk, text: str, workspace: Path
    ) -> str:
        if self._text_normalizer is None:
            raise DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR,
                "text normalization is not configured on this server",
            )
        path = workspace / "chunks" / f"{chunk.id}.normalized.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        digest_path = path.with_name(f"{chunk.id}.normalized.src.sha256")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        # A resumed job reuses its accepted output only if it was made from this exact text;
        # chunk ids survive re-chunking, so a stale file could otherwise pass the guard.
        if _nonempty(path):
            reused = path.read_text(encoding="utf-8")
            made_from_text = (
                digest_path.is_file() and digest_path.read_text(encoding="utf-8").strip() == digest
            )
            if made_from_text and normalization_problem(text, reused) is None:
                return reused
            path.unlink(missing_ok=True)
            digest_path.unlink(missing_ok=True)
        cache = self._artifact_cache
        if cache is not None and cache.fill_normalization(
            text=text,
            language=job.target_language,
            destination=path,
            job_id=job.id,
            chunk_id=chunk.id,
        ):
            cached = path.read_text(encoding="utf-8")
            # Entries written before the fidelity guard existed are checked too.
            if normalization_problem(text, cached) is None:
                digest_path.write_text(digest, encoding="utf-8")
                return cached
            path.unlink(missing_ok=True)
        normalizer = self._text_normalizer

        async def _normalize() -> str:
            return await normalizer.normalize(text, job.target_language)

        normalized = await with_chunk_retry(
            _normalize,
            policy=self._retry_policy,
            sleep=self._sleep,
            job_id=job.id,
            chunk_id=chunk.id,
            stage=STAGE_NARRATED,
        )
        problem = normalization_problem(text, normalized)
        if problem is not None:
            # Never narrate or cache an unfaithful rewrite; the original text is safe to read.
            logger.warning(
                "normalization_rejected",
                extra={"job_id": job.id, "chunk_id": chunk.id, "reason": problem},
            )
            return text
        path.write_text(normalized, encoding="utf-8")
        # Written after the output: a crash in between leaves no digest, so nothing is reused.
        digest_path.write_text(digest, encoding="utf-8")
        if cache is not None:
            cache.store_normalization(
                text=text,
                language=job.target_language,
                source=path,
                job_id=job.id,
                chunk_id=chunk.id,
            )
        return normalized

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

                    async def _pick() -> str:
                        await refresh_engine_voices(self._tts, backoff_seconds=0)
                        try:
                            return select_voice(self._tts, job.target_language, job.voice)
                        except DomainError as exc:
                            # Engine supports the language but listed no voices: its voice
                            # service is down, which is transient (retryable), not unsupported.
                            if (
                                exc.error_type is ErrorType.UNSUPPORTED_LANGUAGE
                                and self._tts.languages()
                                and not self._tts.all_voices()
                            ):
                                raise DomainError(
                                    ErrorType.TTS_FAILED, "voice service unavailable"
                                ) from exc
                            raise

                    selected_voice = await with_chunk_retry(
                        _pick,
                        policy=self._retry_policy,
                        sleep=self._sleep,
                        job_id=job.id,
                        chunk_id=chunk.id,
                        stage=STAGE_TTS,
                    )
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
                    if settings.speed != 1.0 and not getattr(self._tts, "honours_speed", True):
                        # Engine ignores `speed`: apply it as tempo (cache keeps raw audio).
                        sped = normalized_path.with_name(f"{chunk.id}.speed.{ext}")
                        await self._audio.adjust_tempo(
                            normalized_path,
                            sped,
                            settings.speed,
                            output_format=job.output_format,
                            bitrate_kbps=job.output_bitrate_kbps,
                        )
                        _place_artifact(sped, normalized_path)
                    async with lock:
                        checkpoints.record(chunk.id, STAGE_NORMALIZED, normalized_path)

                async with lock:
                    completed_count += 1
                    already_completed_ids.add(chunk.id)
                    if job.status is JobStatus.GENERATING_AUDIO:
                        job = self._with_chunk_progress(job, chunk_current=completed_count)
                        await self._jobs.save(job)
                    logger.info(
                        "pipeline_chunk_done",
                        extra={
                            "job_id": job.id,
                            "chunk_id": chunk.id,
                            "stage": "generating_audio",
                        },
                    )
                await self._assemble_chapters(
                    job, chunks, workspace, checkpoints, only_chunk_index=chunk.index
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

        await self._assemble_chapters(job, chunks, workspace, checkpoints)
        if queue.empty():
            return job

        self._active_chunk_id = None
        effective_concurrency = min(
            self._tts_concurrency,
            getattr(self._tts, "max_concurrency", self._tts_concurrency),
        )
        await self._drain_chunk_queue(
            queue,
            _process_chunk,
            worker_count=min(effective_concurrency, queue.qsize()),
        )
        await self._assemble_chapters(job, chunks, workspace, checkpoints)
        if self._budget_exhausted() and not queue.empty():
            raise _BudgetYield(job)
        return job

    def _chunks_for(
        self,
        text: str,
        workspace: Path,
        checkpoints: CheckpointStore,
        *,
        forget_on_plan: bool,
        legacy_if_in_flight: bool,
        narration: bool = False,
    ) -> list[Chunk]:
        """Reuse the saved split. A job already in flight without one keeps the old packer.

        The manifest is written once, with the plan, before any chunk work. A later
        deploy must not remap ``chunk-NNN`` checkpoints onto different text.
        """
        planned = load_narrate_plan(workspace)
        if planned is not None:
            if forget_on_plan:
                self._drop_stale_chapters(
                    workspace,
                    checkpoints,
                    _forget_chunks_whose_text_changed(workspace, checkpoints, planned),
                )
            return planned
        if legacy_if_in_flight and _legacy_chunk_progress(workspace, checkpoints):
            return chunk_source(text, max_chars=self._max_chars)
        if narration:
            source = prepared_narration_text(text)
            chunks, spans = plan_narration_chunks(text, max_chars=self._max_chars)
        else:
            source = text
            chunks, spans = plan_chapter_chunks(split_chapters(source), max_chars=self._max_chars)
        if not chunks:
            if source.strip():
                return chunk_source(source, max_chars=self._max_chars)
            return []
        if self._chapter_manifest_factory is not None and spans:
            store = self._chapter_manifest_factory(workspace)
            store.save(build_chapter_manifest(spans, max_chars=self._max_chars))
        save_narrate_plan(workspace, chunks)
        if forget_on_plan:
            self._drop_stale_chapters(
                workspace,
                checkpoints,
                _forget_chunks_whose_text_changed(workspace, checkpoints, chunks),
            )
        return chunks

    async def _assemble_chapters(
        self,
        job: Job,
        chunks: list[Chunk],
        workspace: Path,
        checkpoints: CheckpointStore,
        *,
        only_chunk_index: int | None = None,
    ) -> None:
        manifest, store = self._chapter_state(workspace)
        if manifest is None or store is None:
            return
        async with self._chapter_lock:
            await assemble_ready_chapters(
                audio=self._audio,
                job=job,
                chunks=chunks,
                workspace=workspace,
                checkpoints=checkpoints,
                manifest=manifest,
                store=store,
                only_chunk_index=only_chunk_index,
            )

    def _drop_stale_chapters(
        self,
        workspace: Path,
        checkpoints: CheckpointStore,
        chunk_indexes: list[int],
    ) -> None:
        factory = self._chapter_manifest_factory
        if factory is None or not chunk_indexes:
            return
        invalidate_chapters_for_chunks(
            checkpoints=checkpoints,
            store=factory(workspace),
            chunk_indexes=chunk_indexes,
        )
        # The next assembly loads the file once. Drop a copy taken before this.
        self._manifest_bound = False
        self._manifest = None
        self._manifest_store = None

    def _chapter_state(
        self, workspace: Path
    ) -> tuple[ChapterManifest | None, ChapterManifestStore | None]:
        """Load the manifest once per run. Later chunks only update that object."""
        factory = self._chapter_manifest_factory
        if factory is None:
            return None, None
        if not self._manifest_bound:
            store = factory(workspace)
            self._manifest_store = store
            self._manifest = store.load()
            self._manifest_bound = True
        return self._manifest, self._manifest_store

    async def _merge(self, job: Job, chunks: list[Chunk], workspace: Path) -> Job:
        ext = job.output_format.value
        sources: list[Path] = []
        pause: Path | None = None
        for position, chunk in enumerate(chunks):
            if (
                job.mode is JobMode.NARRATE
                and self._chapter_pause_ms
                and position
                and chunk.chapter
                and chunk.chapter != chunks[position - 1].chapter
            ):
                if pause is None:
                    pause = workspace / "audio" / f"chapter-pause.{ext}"
                    await self._audio.generate_silence(
                        self._chapter_pause_ms,
                        pause,
                        output_format=job.output_format,
                        bitrate_kbps=job.output_bitrate_kbps,
                    )
                sources.append(pause)
            sources.append(workspace / "audio" / f"{chunk.id}.normalized.{ext}")
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

    async def _drain_chunk_queue(
        self,
        queue: asyncio.Queue[Chunk],
        process: Callable[[Chunk], Awaitable[None]],
        *,
        worker_count: int,
    ) -> None:
        """Run queued chunks. Past the deadline, finish in-flight work and stop pulling."""

        async def _worker() -> None:
            while True:
                if self._budget_exhausted():
                    return
                try:
                    chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                await process(chunk)
                queue.task_done()

        count = min(max(worker_count, 0), queue.qsize())
        if count == 0:
            return
        tasks = [asyncio.create_task(_worker()) for _ in range(count)]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _begin_merge(self, job: Job, input_count: int) -> Job:
        if self._defer_heavy_step(input_count):
            raise _BudgetYield(job)
        return await self._advance(job, JobStatus.MERGING)

    def _budget_exhausted(self) -> bool:
        deadline = self._deadline
        return deadline is not None and time.monotonic() >= deadline

    def _remaining_budget(self) -> float | None:
        if self._deadline is None:
            return None
        return self._deadline - time.monotonic()

    def _defer_heavy_step(self, input_count: int) -> bool:
        """Yield before merge only after this run recorded new checkpoints.

        Setup (parse, chunk, checkpoint stats) on a large book can consume the
        small tail of a fresh run. Deferring then, with no new checkpoint, makes
        every later run look stalled and the job fails before concat. A run that
        did not record anything always starts the merge.
        """
        checkpoints = self._checkpoints
        if checkpoints is None or checkpoints.entry_count() <= self._run_baseline:
            return False
        remaining = self._remaining_budget()
        window = self._budget_window
        if remaining is None or window is None or window <= 0:
            return False
        raw = max(120.0, 2.0 * max(input_count, 1))
        cap = window if window <= 5.0 else max(1.0, window - 5.0)
        estimate = min(raw, cap)
        if remaining >= estimate:
            return False
        return remaining + 1.0 < window

    def _with_chunk_progress(self, job: Job, *, chunk_current: int) -> Job:
        """Reset crash recovery when this run recorded a new checkpoint."""
        marker = job.progress_marker
        checkpoints = self._checkpoints
        if checkpoints is not None:
            live = checkpoints.entry_count()
            if live > self._run_baseline:
                marker = max(marker, live)
        recovery = 0 if marker > job.progress_marker else job.recovery_attempts
        return replace(
            job,
            chunk_current=chunk_current,
            progress_marker=marker,
            recovery_attempts=recovery,
        )

    async def _yield(self, job: Job, checkpoints: CheckpointStore) -> Job:
        fresh = await self._jobs.get(job.id)
        current = fresh if fresh is not None else job
        if is_terminal(current.status):
            return current
        marker = max(current.progress_marker, checkpoints.entry_count())
        if marker > self._run_baseline:
            stalled = 0
            recovery = 0
        else:
            stalled = current.stalled_runs + 1
            recovery = current.recovery_attempts
        current = replace(
            current,
            stalled_runs=stalled,
            recovery_attempts=recovery,
            progress_marker=marker,
            continue_requested=False,
        )
        if stalled >= self._max_stalled_runs:
            await self._jobs.save(current)
            return await self._fail(
                current,
                ErrorType.TIMEOUT,
                f"no progress in {self._max_stalled_runs} consecutive runs",
            )
        if current.run_count >= self._max_job_runs:
            await self._jobs.save(current)
            return await self._fail(
                current,
                ErrorType.TIMEOUT,
                f"job exceeded max runs ({self._max_job_runs})",
            )
        current = replace(current, continue_requested=True)
        await self._jobs.save(current)
        logger.info(
            "pipeline_yielded",
            extra={
                "chunk_current": current.chunk_current,
                "chunk_id": None,
                "chunk_total": current.chunk_total,
                "job_id": current.id,
                "status": current.status.value,
            },
        )
        return current

    async def _commit_progress(self, job: Job, checkpoints: CheckpointStore) -> Job:
        if job.status is JobStatus.FAILED:
            return job
        fresh = await self._jobs.get(job.id)
        current = fresh if fresh is not None else job
        if current.status is JobStatus.FAILED:
            return current
        marker = max(current.progress_marker, checkpoints.entry_count())
        updated = current
        changed = False
        if marker > self._run_baseline and (
            current.recovery_attempts != 0
            or current.stalled_runs != 0
            or current.progress_marker != marker
        ):
            updated = replace(
                updated,
                recovery_attempts=0,
                stalled_runs=0,
                progress_marker=marker,
            )
            changed = True
        elif marker > current.progress_marker:
            updated = replace(updated, progress_marker=marker)
            changed = True
        if updated.continue_requested:
            updated = replace(updated, continue_requested=False)
            changed = True
        if changed:
            await self._jobs.save(updated)
        return updated

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
            job = self._with_chunk_progress(
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
        fresh = await self._jobs.get(job.id)
        current = fresh if fresh is not None else job
        stage = current.status.value
        if current.status is not JobStatus.FAILED:
            stage = current.status.value
            assert_legal_transition(current.status, JobStatus.FAILED)
            current = replace(
                current,
                status=JobStatus.FAILED,
                error_type=error_type,
                message=message,
                error_stage=stage,
            )
            await self._jobs.save(current)
        job = current
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
