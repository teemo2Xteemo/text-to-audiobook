"""Explicit Edge continuation after a voice-engine failure.

Nothing here switches engines on its own. The caller has already decided the user
asked to continue. Ready chapter files and their checkpoints stay. Unfinished
chapters lose TTS and normalize checkpoints so one chapter is not mixed from two
engines. Translation checkpoints stay.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.application.pipeline.checkpoint import (
    STAGE_CHAPTER,
    STAGE_NORMALIZED,
    STAGE_TTS,
    CheckpointStore,
)
from app.application.pipeline.narrate_plan import load_narrate_plan
from app.domain.chapters import (
    ChapterManifest,
    ChapterRecord,
    ChapterStatus,
    chapter_checkpoint_id,
    chapter_disk_name,
)
from app.domain.chunking import Chunk
from app.domain.errors import ErrorType
from app.domain.jobs import Job, JobMode, JobStatus
from app.domain.ports import ChapterManifestStore

EDGE_ENGINE = "edge"
EDGE_CHAPTER_NOTE = "Continued with Edge after the original voice engine failed."
SINGLE_SPAN_WARNING = (
    "This story has no chapter breaks, so the rest of the audio is read with Edge."
)
LATER_CHAPTERS_WARNING = "Later chapters use Edge. Finished chapters keep their original voice."

_AUDIO_STAGES = frozenset({JobStatus.PREPARING_TTS.value, JobStatus.GENERATING_AUDIO.value})
_ENGINE_ERRORS = frozenset(
    {
        ErrorType.TTS_FAILED,
        ErrorType.PROVIDER_RATE_LIMIT,
        ErrorType.PROVIDER_CONFIGURATION_ERROR,
        ErrorType.TIMEOUT,
    }
)
_TRANSLATION_MARKERS = (
    "gemini",
    "groq",
    "openrouter",
    "ollama",
    "nllb",
    "translation_provider",
    "when translation_provider",
)
_TTS_MARKERS = ("tts", "fish", "vieneu", "quota", "sidecar", "voice service", "voice engine")
_CHUNK_ID = re.compile(r"^chunk-\d+$")
_RESYNTH_STAGES = frozenset({STAGE_TTS, STAGE_NORMALIZED})


def continuation_warning(manifest: ChapterManifest | None) -> str:
    if manifest is None or len(manifest.chapters) <= 1:
        return SINGLE_SPAN_WARNING
    return LATER_CHAPTERS_WARNING


def is_tts_engine_failure(job: Job) -> bool:
    """True when this failure is the pinned voice engine, not translation or the run budget."""
    if job.error_type not in _ENGINE_ERRORS:
        return False
    stage = job.error_stage
    message = (job.message or "").lower()
    if job.error_type is ErrorType.TTS_FAILED:
        return stage is None or stage in _AUDIO_STAGES
    if job.error_type is ErrorType.TIMEOUT:
        return "tts" in message and (stage is None or stage in _AUDIO_STAGES)
    if _looks_like_translation(message) and not _looks_like_tts(message):
        return False
    if stage in _AUDIO_STAGES:
        return True
    if job.error_type is ErrorType.PROVIDER_RATE_LIMIT and stage is None:
        return job.mode is JobMode.NARRATE or _looks_like_tts(message)
    if job.error_type is ErrorType.PROVIDER_CONFIGURATION_ERROR and stage in {
        None,
        JobStatus.QUEUED.value,
    }:
        pinned = (job.tts_engine or "").strip().lower()
        return _looks_like_tts(message) or pinned not in {"", "edge", "fake"}
    return _looks_like_tts(message)


def manifest_has_engine_chapter_failure(manifest: ChapterManifest | None) -> bool:
    if manifest is None:
        return False
    return any(_chapter_engine_failure(chapter) for chapter in manifest.chapters)


def retarget_unfinished_chapters(
    *,
    workspace: Path,
    job: Job,
    manifest_store: ChapterManifestStore | None,
    checkpoints: CheckpointStore,
    previous_engine: str,
) -> str:
    """Keep ready chapter audio. Drop voice-engine audio for every other span.

    Returns the warning the UI shows. A book with one span (or no manifest) is
    re-read entirely with Edge so the merged file is not Fish audio plus Edge audio.
    """
    manifest = manifest_store.load() if manifest_store is not None else None
    warning = continuation_warning(manifest)
    plan = load_narrate_plan(workspace)
    ext = job.output_format.value
    if manifest is None or not manifest.chapters:
        _drop_audio_chunks(workspace, checkpoints, _chunk_ids(plan, checkpoints), ext)
        _unlink_output(workspace, ext)
        return warning

    drop_ids: set[str] = set()
    changed = False
    for record in manifest.chapters:
        if record.status is ChapterStatus.READY:
            if not record.tts_engine:
                record.tts_engine = previous_engine
                changed = True
            continue
        owned = _ids_for_record(record, plan, checkpoints)
        drop_ids.update(owned)
        drop_ids.add(chapter_checkpoint_id(record.ordinal))
        for chunk_id in owned:
            _unlink_chunk_audio(workspace, chunk_id, ext)
        _unlink_under(workspace, workspace / chapter_disk_name(record.ordinal, ext))
        if record.status is not ChapterStatus.PENDING:
            record.invalidate()
        record.tts_engine = EDGE_ENGINE
        record.note = EDGE_CHAPTER_NOTE
        changed = True
    if drop_ids:
        # Chunk rows are tts/normalized. The chapter file row is stage "chapter".
        checkpoints.drop(chunk_ids=drop_ids, stages=_RESYNTH_STAGES | {STAGE_CHAPTER})
    if changed and manifest_store is not None:
        manifest_store.save(manifest)
    _unlink_output(workspace, ext)
    return warning


def _chapter_engine_failure(record: ChapterRecord) -> bool:
    if record.status is not ChapterStatus.FAILED or not record.error_type:
        return False
    try:
        error_type = ErrorType(record.error_type)
    except ValueError:
        return False
    if error_type is ErrorType.TTS_FAILED:
        return True
    message = (record.message or "").lower()
    if error_type is ErrorType.TIMEOUT:
        return "tts" in message
    if error_type not in {ErrorType.PROVIDER_RATE_LIMIT, ErrorType.PROVIDER_CONFIGURATION_ERROR}:
        return False
    return _looks_like_tts(message) or not _looks_like_translation(message)


def _looks_like_translation(message: str) -> bool:
    return any(marker in message for marker in _TRANSLATION_MARKERS)


def _looks_like_tts(message: str) -> bool:
    return any(marker in message for marker in _TTS_MARKERS)


def _chunk_ids(plan: list[Chunk] | None, checkpoints: CheckpointStore) -> set[str]:
    ids = {chunk.id for chunk in plan} if plan else set()
    for entry in checkpoints.load():
        if _CHUNK_ID.match(entry.chunk_id):
            ids.add(entry.chunk_id)
    return ids


def _ids_for_record(
    record: ChapterRecord,
    plan: list[Chunk] | None,
    checkpoints: CheckpointStore,
) -> set[str]:
    if plan:
        return {
            chunk.id for chunk in plan if record.chunk_first <= chunk.index <= record.chunk_last
        }
    owned: set[str] = set()
    for entry in checkpoints.load():
        if not _CHUNK_ID.match(entry.chunk_id):
            continue
        index = _chunk_index(entry.chunk_id)
        if index is not None and record.chunk_first <= index <= record.chunk_last:
            owned.add(entry.chunk_id)
    return owned


def _chunk_index(chunk_id: str) -> int | None:
    try:
        return int(chunk_id.removeprefix("chunk-"))
    except ValueError:
        return None


def _drop_audio_chunks(
    workspace: Path,
    checkpoints: CheckpointStore,
    chunk_ids: set[str],
    ext: str,
) -> None:
    if not chunk_ids:
        return
    checkpoints.drop(chunk_ids=chunk_ids, stages=_RESYNTH_STAGES)
    for chunk_id in chunk_ids:
        _unlink_chunk_audio(workspace, chunk_id, ext)


def _unlink_chunk_audio(workspace: Path, chunk_id: str, ext: str) -> None:
    if not _CHUNK_ID.match(chunk_id):
        return
    audio = workspace / "audio"
    for name in (f"{chunk_id}.{ext}", f"{chunk_id}.normalized.{ext}", f"{chunk_id}.speed.{ext}"):
        _unlink_under(workspace, audio / name)


def _unlink_output(workspace: Path, ext: str) -> None:
    _unlink_under(workspace, workspace / f"output.{ext}")
    _unlink_under(workspace, workspace / "output.srt")


def _unlink_under(root: Path, path: Path) -> None:
    try:
        resolved = path.resolve()
        base = root.resolve()
    except OSError:
        return
    if not resolved.is_relative_to(base):
        return
    resolved.unlink(missing_ok=True)
