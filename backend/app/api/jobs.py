from __future__ import annotations

import asyncio
import base64
import threading
import time
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.deps import (
    get_job_service,
    get_language_detector,
    get_settings,
    get_tts_provider,
)
from app.api.errors import envelope
from app.api.parse import parse_analyze, parse_create_job, parse_detect_language
from app.api.schemas import (
    AnalyzeResponse,
    ChapterSummary,
    DetectLanguageResponse,
    JobCreatedResponse,
    JobStatusResponse,
    LanguageCandidate,
    TTSPreviewRequest,
    TTSPreviewResponse,
)
from app.application.jobs import JobService
from app.application.pipeline.chapters import split_chapters
from app.config.settings import Settings
from app.domain.audio import TTSSettings, ensure_valid_speed
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import JobStatus, OutputFormat
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
    return JobStatusResponse.from_job(job, subtitles_url=subtitles_url)


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

    chapters = split_chapters(text)
    chapters_count = len(chapters)
    chapters_preview = [
        ChapterSummary(index=c.index, title=c.title, char_count=c.char_count) for c in chapters[:10]
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
        chapters_preview=chapters_preview,
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
) -> TTSPreviewResponse:
    if not req.text.strip():
        raise DomainError(ErrorType.INVALID_INPUT, "text is required")
    if not req.voice.strip():
        raise DomainError(ErrorType.INVALID_INPUT, "voice is required")
    ensure_valid_speed(req.speed)

    client_ip = request.client.host if request.client else "unknown"
    _preview_rate_limiter.check(client_ip, settings.tts_preview_rate_per_minute)

    preview_text = truncate_preview_text(req.text.strip())

    try:
        async with asyncio.timeout(float(settings.tts_preview_timeout_seconds)):
            artifact = await tts.synthesize(
                preview_text,
                language="",
                voice=req.voice,
                settings=TTSSettings(speed=req.speed),
            )
    except TimeoutError as exc:
        raise DomainError(ErrorType.TIMEOUT, "TTS preview timed out") from exc

    if not artifact.path.is_file():
        raise DomainError(ErrorType.STORAGE_FAILED, "synthesized audio artifact missing")

    try:
        audio_bytes = artifact.path.read_bytes()
    finally:
        artifact.path.unlink(missing_ok=True)

    audio_base64 = base64.b64encode(audio_bytes).decode("ascii")
    return TTSPreviewResponse(audio_base64=audio_base64, mime_type="audio/mpeg")


def _require_uuid(job_id: str) -> None:
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=envelope(ErrorType.INVALID_INPUT, "job_id must be a UUID"),
        ) from None
