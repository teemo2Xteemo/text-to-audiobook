"""Assemble one audio file per chapter once its chunks are normalized."""

from __future__ import annotations

import logging
from pathlib import Path

from app.application.pipeline.checkpoint import STAGE_CHAPTER, STAGE_NORMALIZED, CheckpointStore
from app.application.pipeline.merge import merge_artifacts
from app.domain.chapters import (
    ChapterManifest,
    ChapterRecord,
    ChapterStatus,
    chapter_checkpoint_id,
    chapter_disk_name,
)
from app.domain.chunking import Chunk
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobMode
from app.domain.ports import AudioProcessor, ChapterManifestStore

logger = logging.getLogger(__name__)

_TITLE_LINE_MAX = 80


def refresh_translated_title(record: ChapterRecord, workspace: Path) -> None:
    """Use the translated heading when the first chunk still starts with one."""
    if record.title == "Full text" or record.title_source == "Full text":
        return
    if record.kind == "preface" and not record.title_source.strip():
        return
    path = workspace / "chunks" / f"chunk-{record.chunk_first:03d}.translated.txt"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return
    first = lines[0]
    if 0 < len(first) <= _TITLE_LINE_MAX:
        record.title = first


def invalidate_chapters_for_chunks(
    *,
    checkpoints: CheckpointStore,
    store: ChapterManifestStore,
    chunk_indexes: list[int],
) -> None:
    """Drop chapter files whose chunks were forgotten, so they are built again."""
    if not chunk_indexes:
        return
    manifest = store.load()
    if manifest is None:
        return
    dirty = False
    seen: set[int] = set()
    for index in chunk_indexes:
        record = manifest.record_for_chunk(index)
        if record is None or record.ordinal in seen:
            continue
        seen.add(record.ordinal)
        checkpoints.forget_chunk(chapter_checkpoint_id(record.ordinal))
        if record.status is not ChapterStatus.PENDING:
            record.invalidate()
            dirty = True
    if dirty:
        store.save(manifest)


async def assemble_ready_chapters(
    *,
    audio: AudioProcessor,
    job: Job,
    chunks: list[Chunk],
    workspace: Path,
    checkpoints: CheckpointStore,
    manifest: ChapterManifest,
    store: ChapterManifestStore,
    only_chunk_index: int | None = None,
) -> None:
    """Write ``chapters/{ordinal}.ext`` for every span whose chunks are normalized.

    ``manifest`` is the in-memory copy for this run. The file is saved only when
    a span changes state, not once per chunk. A complete ``chapter-NNNN`` checkpoint
    is not rebuilt. A failed assembly is recorded on that span and re-raised.
    """
    if not manifest.chapters:
        return
    for record in manifest.chapters:
        if only_chunk_index is not None and not (
            record.chunk_first <= only_chunk_index <= record.chunk_last
        ):
            continue
        await _assemble_one(
            audio=audio,
            job=job,
            record=record,
            chunks=chunks,
            workspace=workspace,
            checkpoints=checkpoints,
            manifest=manifest,
            store=store,
        )


async def _assemble_one(
    *,
    audio: AudioProcessor,
    job: Job,
    record: ChapterRecord,
    chunks: list[Chunk],
    workspace: Path,
    checkpoints: CheckpointStore,
    manifest: ChapterManifest,
    store: ChapterManifestStore,
) -> None:
    key = chapter_checkpoint_id(record.ordinal)
    if checkpoints.is_complete(key, STAGE_CHAPTER):
        if record.status is not ChapterStatus.READY:
            _become(record, ChapterStatus.READY)
            await _fill_stats(record, workspace, job, audio)
            _remember_engine(record, job)
            store.save(manifest)
        return
    owned = [chunk for chunk in chunks if record.chunk_first <= chunk.index <= record.chunk_last]
    if not owned:
        return
    if not all(checkpoints.is_complete(chunk.id, STAGE_NORMALIZED) for chunk in owned):
        return
    if job.mode is JobMode.TRANSLATE:
        refresh_translated_title(record, workspace)
    if record.status is not ChapterStatus.GENERATING:
        _become(record, ChapterStatus.GENERATING)
    record.attempts += 1
    ext = job.output_format.value
    relative = chapter_disk_name(record.ordinal, ext)
    destination = workspace / relative
    sources = [workspace / "audio" / f"{chunk.id}.normalized.{ext}" for chunk in owned]
    try:
        await merge_artifacts(
            audio,
            sources,
            destination,
            output_format=job.output_format,
            bitrate_kbps=job.output_bitrate_kbps,
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            raise DomainError(
                ErrorType.AUDIO_PROCESSING_FAILED,
                f"chapter {record.ordinal} assembly failed",
            )
        duration_ms = await audio.get_duration_ms(destination)
        checkpoints.record(key, STAGE_CHAPTER, destination)
    except Exception as exc:
        # Re-raise the original error. A disk failure stays STORAGE_FAILED, and a
        # worker timeout must not be rewritten into AUDIO_PROCESSING_FAILED.
        if isinstance(exc, DomainError):
            record.error_type = exc.error_type.value
            record.message = exc.message
            _become(record, ChapterStatus.FAILED)
        elif isinstance(exc, OSError):
            record.error_type = ErrorType.STORAGE_FAILED.value
            record.message = f"chapter {record.ordinal} assembly failed"
            _become(record, ChapterStatus.FAILED)
        elif record.status is not ChapterStatus.PENDING:
            record.invalidate()
        store.save(manifest)
        raise
    record.file = relative
    record.duration_ms = duration_ms
    record.size_bytes = destination.stat().st_size
    record.error_type = None
    record.message = None
    _become(record, ChapterStatus.READY)
    _remember_engine(record, job)
    store.save(manifest)
    logger.info(
        "pipeline_chapter_ready",
        extra={"job_id": job.id, "chunk_id": key, "stage": STAGE_CHAPTER},
    )


def _remember_engine(record: ChapterRecord, job: Job) -> None:
    if record.tts_engine or not job.tts_engine:
        return
    record.tts_engine = job.tts_engine


def _become(record: ChapterRecord, status: ChapterStatus) -> None:
    if record.status is status:
        return
    if record.status is not ChapterStatus.GENERATING and status is not ChapterStatus.GENERATING:
        record.mark(ChapterStatus.GENERATING)
    if record.status is not status:
        record.mark(status)


async def _fill_stats(
    record: ChapterRecord,
    workspace: Path,
    job: Job,
    audio: AudioProcessor,
) -> None:
    relative = record.file or chapter_disk_name(record.ordinal, job.output_format.value)
    path = workspace / relative
    record.file = relative
    if not path.is_file():
        return
    record.size_bytes = path.stat().st_size
    if record.duration_ms is None:
        record.duration_ms = await audio.get_duration_ms(path)
