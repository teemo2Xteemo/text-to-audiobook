from __future__ import annotations

import asyncio
import base64
import threading
import time
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import (
    get_job_service,
    get_language_detector,
    get_settings,
    get_tts_engines,
    get_tts_provider,
)
from app.api.errors import envelope
from app.api.parse import parse_analyze, parse_create_job, parse_detect_language
from app.api.schemas import (
    AnalyzeResponse,
    ChapterListItem,
    ChapterListResponse,
    ChapterSummary,
    DetectLanguageResponse,
    JobCreatedResponse,
    JobStatusResponse,
    LanguageCandidate,
    TTSPreviewRequest,
    TTSPreviewResponse,
)
from app.application.capabilities import engine_label
from app.application.jobs import JobService
from app.application.pipeline.chapter_zip import iter_stored_zip
from app.application.pipeline.chapters import ChapterKind, split_chapters_detailed
from app.config.factory import build_artifact_cache
from app.config.settings import Settings
from app.domain.audio import TTSSettings, ensure_valid_speed
from app.domain.chapters import ChapterManifest, ChapterStatus
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.ports import LanguageDetector, TTSProvider

router = APIRouter()

_MEDIA_TYPES = {
    OutputFormat.MP3: "audio/mpeg",
    OutputFormat.WAV: "audio/wav",
}


@router.post("/api/jobs", status_code=202, response_model=JobCreatedResponse)
async def create_job(
    request: Request,
    settings: Settings = Depends(get_settings),
    service: JobService = Depends(get_job_service),
) -> JobCreatedResponse:
    command = await parse_create_job(request, settings)
    job = await service.create(command)
    return JobCreatedResponse(job_id=job.id, status=job.status)


@router.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobStatusResponse:
    _require_uuid(job_id)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
        )
    subtitles_url: str | None = None
    if job.status is JobStatus.COMPLETED:
        subtitles_path = service.output_subtitles_path(job)
        if subtitles_path.is_file() and subtitles_path.stat().st_size > 0:
            subtitles_url = f"/api/jobs/{job.id}/subtitles"
    total, ready, failed, chapters_url, zip_url = _chapter_fields(service, job)
    return JobStatusResponse.from_job(
        job,
        subtitles_url=subtitles_url,
        chapters_total=total,
        chapters_ready=ready,
        chapters_failed=failed,
        chapters_url=chapters_url,
        zip_url=zip_url,
    )


@router.get("/api/jobs/{job_id}/audio")
async def download_job_audio(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> FileResponse:
    _require_uuid(job_id)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
        )
    if job.status is not JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.INVALID_INPUT, "audio not ready"),
        )
    path = service.output_audio_path(job)
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.STORAGE_FAILED, "audio artifact missing"),
        )
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[job.output_format],
        filename=f"{job.id}.{job.output_format.value}",
        content_disposition_type="inline",
    )


@router.get("/api/jobs/{job_id}/subtitles")
async def download_job_subtitles(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> FileResponse:
    _require_uuid(job_id)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
        )
    if job.status is not JobStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.INVALID_INPUT, "subtitles not ready"),
        )
    path = service.output_subtitles_path(job)
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.STORAGE_FAILED, "subtitles artifact missing"),
        )
    return FileResponse(
        path,
        media_type="application/x-subrip",
        filename=f"{job.id}.srt",
        content_disposition_type="attachment",
    )


@router.post("/api/jobs/{job_id}/retry", status_code=202, response_model=JobCreatedResponse)
async def retry_job(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> JobCreatedResponse:
    _require_uuid(job_id)
    try:
        retried = await service.retry(job_id)
    except DomainError as exc:
        if exc.error_type is ErrorType.INVALID_INPUT and exc.message == "job not found":
            raise HTTPException(
                status_code=404,
                detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
            ) from exc
        if exc.error_type is ErrorType.INVALID_INPUT and exc.message == "job cannot be retried":
            raise HTTPException(
                status_code=409,
                detail=envelope(ErrorType.INVALID_INPUT, "job cannot be retried"),
            ) from exc
        raise
    return JobCreatedResponse(job_id=retried.id, status=retried.status)


@router.get("/api/jobs/{job_id}/chapters", response_model=ChapterListResponse)
async def list_job_chapters(
    job_id: str,
    status: str | None = None,
    q: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    service: JobService = Depends(get_job_service),
) -> ChapterListResponse:
    job = await _require_job(job_id, service)
    manifest = service.load_chapter_manifest(job.id)
    if manifest is None:
        return ChapterListResponse(
            job_id=job.id,
            total=0,
            ready=0,
            failed=0,
            offset=offset,
            limit=limit,
            matched=0,
            partial=False,
            items=[],
        )
    items = _chapter_items(service, job, manifest)
    total, ready, failed = manifest.counts()
    if status is not None:
        allowed = {item.value for item in ChapterStatus}
        if status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=envelope(ErrorType.INVALID_INPUT, "invalid chapter status"),
            )
        items = [item for item in items if item.status == status]
    if q is not None and q.strip():
        needle = q.strip().casefold()
        items = [
            item
            for item in items
            if needle in item.title.casefold()
            or needle in item.title_source.casefold()
            or needle in f"{item.ordinal:04d}"
        ]
    matched = len(items)
    page = items[offset : offset + limit]
    return ChapterListResponse(
        job_id=job.id,
        total=total,
        ready=ready,
        failed=failed,
        matched=matched,
        offset=offset,
        limit=limit,
        partial=ready < total,
        items=page,
    )


@router.get("/api/jobs/{job_id}/chapters/{ordinal}/audio")
async def download_chapter_audio(
    job_id: str,
    ordinal: int,
    download: int = Query(default=0),
    service: JobService = Depends(get_job_service),
) -> FileResponse:
    job = await _require_job(job_id, service)
    if ordinal < 1:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "chapter not found"),
        )
    manifest = service.load_chapter_manifest(job.id)
    record = None if manifest is None else manifest.by_ordinal(ordinal)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "chapter not found"),
        )
    if record.status is not ChapterStatus.READY:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.INVALID_INPUT, "chapter not ready"),
        )
    path = service.chapter_audio_path(job, ordinal)
    if not path.is_file() or path.stat().st_size == 0:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.STORAGE_FAILED, "chapter artifact missing"),
        )
    total = len(manifest.chapters) if manifest is not None else ordinal
    filename = service.chapter_download_name(job, ordinal, record.title, total)
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[job.output_format],
        filename=filename,
        content_disposition_type=disposition,
    )


@router.get("/api/jobs/{job_id}/chapters.zip")
async def download_chapters_zip(
    job_id: str,
    service: JobService = Depends(get_job_service),
) -> StreamingResponse:
    job = await _require_job(job_id, service)
    manifest = service.load_chapter_manifest(job.id)
    total = 0 if manifest is None else len(manifest.chapters)
    entries: list[tuple[str, Path]] = []
    if manifest is not None:
        for record in sorted(manifest.chapters, key=lambda item: item.ordinal):
            if record.status is not ChapterStatus.READY:
                continue
            path = service.chapter_audio_path(job, record.ordinal)
            if not path.is_file() or path.stat().st_size == 0:
                continue
            name = service.chapter_download_name(job, record.ordinal, record.title, total)
            entries.append((name, path))
    if not entries:
        raise HTTPException(
            status_code=409,
            detail=envelope(ErrorType.INVALID_INPUT, "no chapters ready"),
        )
    included = len(entries)
    partial = included < total

    filename = f"{job.id}-chapters.zip"
    return StreamingResponse(
        iter_stored_zip(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Chapters-Partial": "true" if partial else "false",
            "X-Chapters-Included": f"{included}/{total}",
        },
    )


async def _require_job(job_id: str, service: JobService) -> Job:
    _require_uuid(job_id)
    job = await service.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail=envelope(ErrorType.INVALID_INPUT, "job not found"),
        )
    return job


def _chapter_fields(service: JobService, job: Job) -> tuple[int, int, int, str | None, str | None]:
    manifest = service.load_chapter_manifest(job.id)
    if manifest is None:
        return 0, 0, 0, None, None
    total, ready, failed = manifest.counts()
    chapters_url = f"/api/jobs/{job.id}/chapters"
    zip_url = f"/api/jobs/{job.id}/chapters.zip" if ready > 0 else None
    return total, ready, failed, chapters_url, zip_url


def _chapter_items(
    service: JobService, job: Job, manifest: ChapterManifest
) -> list[ChapterListItem]:
    total = len(manifest.chapters)
    items: list[ChapterListItem] = []
    for record in manifest.chapters:
        ready = record.status is ChapterStatus.READY
        items.append(
            ChapterListItem(
                ordinal=record.ordinal,
                index=record.ordinal,
                kind=record.kind,  # type: ignore[arg-type]
                chapter_number=record.chapter_number,
                title=record.title,
                title_source=record.title_source,
                status=record.status.value,
                chunk_first=record.chunk_first,
                chunk_last=record.chunk_last,
                duration_ms=record.duration_ms,
                size_bytes=record.size_bytes,
                error_type=record.error_type,
                message=record.message,
                filename=service.chapter_download_name(job, record.ordinal, record.title, total),
                download_url=(
                    f"/api/jobs/{job.id}/chapters/{record.ordinal}/audio" if ready else None
                ),
            )
        )
    return items


async def _resolve_language_candidates(
    sample: str,
    detector: LanguageDetector,
    threshold: float,
) -> tuple[str, float, bool, list[LanguageCandidate]]:
    candidates_raw = await detector.detect_candidates(sample)
    if not candidates_raw:
        try:
            detected = await detector.detect(sample)
            candidates_raw = [(detected.language_code, detected.confidence)]
        except DomainError:
            candidates_raw = []

    candidates = [
        LanguageCandidate(language=lang, confidence=conf) for lang, conf in candidates_raw
    ]
    if candidates:
        top_lang = candidates[0].language
        top_conf = candidates[0].confidence
        needs_confirmation = top_conf < threshold
    else:
        top_lang = "unknown"
        top_conf = 0.0
        needs_confirmation = True

    return top_lang, top_conf, needs_confirmation, candidates


@router.post("/api/detect-language", response_model=DetectLanguageResponse)
async def detect_language(
    request: Request,
    settings: Settings = Depends(get_settings),
    detector: LanguageDetector = Depends(get_language_detector),
) -> DetectLanguageResponse:
    text = await parse_detect_language(request, settings.max_upload_bytes)
    slice_text = text[:8192]
    top_lang, top_conf, needs_confirmation, candidates = await _resolve_language_candidates(
        slice_text, detector, settings.language_confirm_threshold
    )
    preview_lines = [line.strip() for line in slice_text.splitlines() if line.strip()][:5]
    return DetectLanguageResponse(
        language=top_lang,
        confidence=top_conf,
        needs_confirmation=needs_confirmation,
        candidates=candidates,
        preview_lines=preview_lines,
    )


@router.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_text(
    request: Request,
    settings: Settings = Depends(get_settings),
    detector: LanguageDetector = Depends(get_language_detector),
) -> AnalyzeResponse:
    text, encoding = await parse_analyze(request, settings)
    slice_text = text[:8192]
    top_lang, top_conf, needs_confirmation, candidates = await _resolve_language_candidates(
        slice_text, detector, settings.language_confirm_threshold
    )

    detailed = await run_in_threadpool(split_chapters_detailed, text)
    chapters = detailed.chapters
    # Volumes and the preface are structure, not chapters, even though each has
    # its own ordinal. A book with no headings is one synthetic chapter.
    chapters_count = sum(1 for chapter in chapters if chapter.kind is ChapterKind.CHAPTER)
    preview_limit = 100
    chapters_preview = [
        ChapterSummary(
            index=chapter.index,
            ordinal=chapter.ordinal,
            kind=chapter.kind.value,
            title=chapter.title,
            char_count=chapter.char_count,
            chapter_number=chapter.chapter_number,
        )
        for chapter in chapters[:preview_limit]
    ]

    char_len = len(text)
    duration_estimate_seconds = round(
        char_len / max(1.0, float(settings.estimate_chars_per_second)), 1
    )
    process_time_estimate_seconds = round(
        duration_estimate_seconds * float(settings.estimate_process_time_ratio), 1
    )
    preview_lines = [line.strip() for line in slice_text.splitlines() if line.strip()][:5]

    return AnalyzeResponse(
        encoding=encoding,
        language=top_lang,
        confidence=top_conf,
        needs_confirmation=needs_confirmation,
        candidates=candidates,
        chapters_count=chapters_count,
        chapters_span_count=len(chapters),
        chapters_preview=chapters_preview,
        chapters_preview_truncated=len(chapters) > preview_limit,
        chapter_detection_confidence=detailed.detection_confidence,
        chapter_detection_uncertain=detailed.uncertain,
        duration_estimate_seconds=duration_estimate_seconds,
        process_time_estimate_seconds=process_time_estimate_seconds,
        preview_lines=preview_lines,
    )


_SENTENCE_BOUNDARIES = (".", "?", "!", "。", "！", "？", "\n")


def truncate_preview_text(text: str, max_chars: int = 300) -> str:
    if len(text) <= max_chars:
        return text
    candidate = text[:max_chars]
    last_boundary_idx = max((candidate.rfind(b) for b in _SENTENCE_BOUNDARIES), default=-1)
    if last_boundary_idx != -1:
        sliced = candidate[: last_boundary_idx + 1].strip()
        if sliced:
            return sliced
    return candidate.strip()


class InMemoryRateLimiter:
    """Thread-safe sliding window in-memory rate limiter per process / client host."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, rate_per_minute: int) -> None:
        now = time.monotonic()
        window_start = now - 60.0
        with self._lock:
            history = [t for t in self._timestamps[key] if t > window_start]
            if len(history) >= rate_per_minute:
                self._timestamps[key] = history
                raise DomainError(
                    ErrorType.PROVIDER_RATE_LIMIT,
                    "Too many preview requests, please try again shortly",
                )
            history.append(now)
            self._timestamps[key] = history
            if len(self._timestamps) > 10_000:
                expired = [
                    k for k, ts in self._timestamps.items() if not ts or ts[-1] <= window_start
                ]
                for k in expired:
                    del self._timestamps[k]

    def reset(self) -> None:
        with self._lock:
            self._timestamps.clear()


_preview_rate_limiter = InMemoryRateLimiter()


@router.post("/api/tts/preview", response_model=TTSPreviewResponse)
async def tts_preview(
    req: TTSPreviewRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    tts: TTSProvider = Depends(get_tts_provider),
    engines: dict[str, TTSProvider | None] = Depends(get_tts_engines),
) -> TTSPreviewResponse:
    if not req.text.strip():
        raise DomainError(ErrorType.INVALID_INPUT, "text is required")
    if not req.voice.strip():
        raise DomainError(ErrorType.INVALID_INPUT, "voice is required")
    ensure_valid_speed(req.speed)

    client_ip = request.client.host if request.client else "unknown"
    _preview_rate_limiter.check(client_ip, settings.tts_preview_rate_per_minute)

    preview_text = truncate_preview_text(req.text.strip())

    engine = (req.tts_engine or "").strip().lower() or None
    pin_settings = settings
    if engine is not None:
        if engine not in engines:
            raise DomainError(ErrorType.INVALID_INPUT, f"tts_engine is not enabled: {engine[:32]}")
        pinned = engines[engine]
        if pinned is None:
            raise DomainError(
                ErrorType.PROVIDER_CONFIGURATION_ERROR,
                f"{engine_label(engine)} is not available right now",
            )
        tts = pinned
        pin_settings = settings.model_copy(update={"tts_provider": engine})

    # Identity follows the engine, so a cached Edge clip never answers a Fish preview.
    cache = build_artifact_cache(pin_settings)
    tts_settings = TTSSettings(speed=req.speed)
    cache_args = {
        "text": preview_text,
        "source_language": "",
        "target_language": "",
        "voice": req.voice,
        "settings": tts_settings,
        "job_id": "preview",
        "chunk_id": "preview",
    }
    cached = settings.storage_path / ".provider-tmp" / "tts" / f"preview-{uuid.uuid4().hex}"
    cached.parent.mkdir(parents=True, exist_ok=True)
    try:
        if cache.fill_tts(destination=cached, **cache_args):
            audio_bytes = cached.read_bytes()
        else:
            try:
                async with asyncio.timeout(float(settings.tts_preview_timeout_seconds)):
                    # shortcut: speed ignored for honours_speed=False engines, upgrade if needed.
                    artifact = await tts.synthesize(
                        preview_text,
                        language="",
                        voice=req.voice,
                        settings=tts_settings,
                    )
            except TimeoutError as exc:
                raise DomainError(ErrorType.TIMEOUT, "TTS preview timed out") from exc
            if not artifact.path.is_file():
                raise DomainError(ErrorType.STORAGE_FAILED, "synthesized audio artifact missing")
            try:
                audio_bytes = artifact.path.read_bytes()
                cache.store_tts(source=artifact.path, **cache_args)
            finally:
                artifact.path.unlink(missing_ok=True)
    finally:
        cached.unlink(missing_ok=True)

    # Sniff the container (cache hits carry no artifact); VieNeu emits WAV, Edge/Fish MP3.
    mime_type = "audio/wav" if audio_bytes[:4] == b"RIFF" else "audio/mpeg"
    audio_base64 = base64.b64encode(audio_bytes).decode("ascii")
    return TTSPreviewResponse(audio_base64=audio_base64, mime_type=mime_type)


def _require_uuid(job_id: str) -> None:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=envelope(ErrorType.INVALID_INPUT, "job_id must be a UUID"),
        ) from None
