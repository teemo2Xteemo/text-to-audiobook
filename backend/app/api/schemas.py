from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_serializer, model_validator

from app.domain.audio import SPEED_DEFAULT, SPEED_MAX, SPEED_MIN, Voice
from app.domain.jobs import Job, JobMode, JobStatus, OutputFormat, SrtTiming
from app.domain.subtitles import DocumentFormat


class ErrorEnvelope(BaseModel):
    error_type: str
    message: str


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    stage: JobStatus
    chunk_current: int
    chunk_total: int
    error_type: str | None
    message: str | None
    source_language: str
    target_language: str
    voice: str | None
    speed: float
    output_format: OutputFormat
    audio_url: str | None = None
    subtitles_url: str | None = None
    translation_provider: str | None = None
    translation_model: str | None = None
    mode: JobMode = JobMode.TRANSLATE
    srt_timing: SrtTiming = SrtTiming.CONTINUOUS
    normalize_text: bool = False
    tts_engine: str | None = None
    run_count: int = 0
    chapters_total: int = 0
    chapters_ready: int = 0
    chapters_failed: int = 0
    chapters_url: str | None = None
    zip_url: str | None = None
    continue_with_edge: bool = False
    tts_mix_warning: str | None = None
    document_url: str | None = None
    document_format: DocumentFormat | None = None
    line_total: int = 0

    @model_serializer(mode="wrap")
    def _omit_absent_pin(self, handler: object) -> dict[str, object]:
        payload = handler(self)
        if not isinstance(payload, dict):
            return {}
        if payload.get("translation_provider") is None and payload.get("translation_model") is None:
            payload.pop("translation_provider", None)
            payload.pop("translation_model", None)
        return payload

    @classmethod
    def from_job(
        cls,
        job: Job,
        *,
        subtitles_url: str | None = None,
        chapters_total: int = 0,
        chapters_ready: int = 0,
        chapters_failed: int = 0,
        chapters_url: str | None = None,
        zip_url: str | None = None,
        continue_with_edge: bool = False,
        tts_mix_warning: str | None = None,
        document_url: str | None = None,
    ) -> JobStatusResponse:
        if job.mode is JobMode.SUBTITLE or job.status is not JobStatus.COMPLETED:
            audio_url = None
        else:
            audio_url = f"/api/jobs/{job.id}/audio"
        return cls(
            job_id=job.id,
            status=job.status,
            stage=job.status,
            chunk_current=job.chunk_current,
            chunk_total=job.chunk_total,
            error_type=job.error_type.value if job.error_type is not None else None,
            message=job.message,
            source_language=job.source_language,
            target_language=job.target_language,
            voice=job.voice,
            speed=job.speed,
            output_format=job.output_format,
            audio_url=audio_url,
            subtitles_url=subtitles_url,
            translation_provider=job.translation_provider,
            translation_model=job.translation_model,
            mode=job.mode,
            srt_timing=job.srt_timing,
            normalize_text=job.normalize_text,
            tts_engine=job.tts_engine,
            run_count=job.run_count,
            chapters_total=chapters_total,
            chapters_ready=chapters_ready,
            chapters_failed=chapters_failed,
            chapters_url=chapters_url,
            zip_url=zip_url,
            continue_with_edge=continue_with_edge,
            tts_mix_warning=tts_mix_warning,
            document_url=document_url,
            document_format=job.document_format,
            line_total=job.line_total,
        )


class CreateJobJsonBody(BaseModel):
    text: str
    source_language: str = ""
    target_language: str = ""
    voice: str | None = None
    speed: float = Field(default=SPEED_DEFAULT, ge=SPEED_MIN, le=SPEED_MAX)
    output_format: str = Field(default=OutputFormat.MP3.value)
    translation_profile_id: str | None = None
    mode: JobMode = JobMode.TRANSLATE
    srt_timing: SrtTiming = SrtTiming.CONTINUOUS
    normalize_text: bool = False
    tts_engine: str | None = None
    document_format: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _ignore_tts_fields_for_subtitle(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        mode = data.get("mode", JobMode.TRANSLATE)
        if mode not in {JobMode.SUBTITLE, JobMode.SUBTITLE.value}:
            return data
        cleaned = dict(data)
        cleaned["speed"] = SPEED_DEFAULT
        cleaned["voice"] = None
        cleaned["output_format"] = OutputFormat.MP3.value
        cleaned["tts_engine"] = None
        cleaned["normalize_text"] = False
        cleaned["srt_timing"] = SrtTiming.CONTINUOUS.value
        return cleaned


class VoiceResponse(BaseModel):
    id: str
    language: str
    label: str
    engine: str = ""

    @classmethod
    def from_voice(cls, voice: Voice) -> VoiceResponse:
        return cls(id=voice.id, language=voice.language, label=voice.label, engine=voice.engine)


class TtsEngineResponse(BaseModel):
    id: str
    label: str
    kind: str
    cost_label: str
    available: bool
    languages: list[str]
    max_concurrency: int


class TranslationProfileResponse(BaseModel):
    id: str
    provider: str
    model: str
    label: str
    badge: str
    description: str
    is_default: bool
    supported_languages: list[str] = Field(default_factory=list)


class UploadLimits(BaseModel):
    max_upload_bytes: int


class CapabilitiesResponse(BaseModel):
    languages: list[str]
    voices: list[VoiceResponse]
    translation_profiles: list[TranslationProfileResponse] = Field(default_factory=list)
    narrate_languages: list[str] = Field(default_factory=list)
    estimate_defaults: dict[str, float] = Field(default_factory=dict)
    text_normalization: bool = False
    tts_engines: list[TtsEngineResponse] = Field(default_factory=list)
    limits: UploadLimits


class LanguageCandidate(BaseModel):
    language: str
    confidence: float


class DetectLanguageRequest(BaseModel):
    text: str


class DetectLanguageResponse(BaseModel):
    language: str
    confidence: float
    needs_confirmation: bool
    candidates: list[LanguageCandidate]
    preview_lines: list[str]


ChapterKindName = Literal["preface", "volume", "chapter"]


class ChapterSummary(BaseModel):
    """One detected span. ``index`` and ``ordinal`` are the same unique 1-based id.

    ``chapter_number`` is the number printed on a chapter heading. It may
    restart after a volume and is null for prefaces and volumes. The document
    score lives on ``AnalyzeResponse.chapter_detection_confidence``.
    """

    index: int
    ordinal: int
    kind: ChapterKindName
    title: str
    char_count: int
    chapter_number: int | None = None


class AnalyzeResponse(BaseModel):
    encoding: str
    language: str
    confidence: float
    needs_confirmation: bool
    candidates: list[LanguageCandidate]
    chapters_count: int
    chapters_span_count: int
    chapters_preview: list[ChapterSummary]
    chapters_preview_truncated: bool = False
    chapter_detection_confidence: float
    chapter_detection_uncertain: bool
    duration_estimate_seconds: float
    process_time_estimate_seconds: float
    preview_lines: list[str]


class ChapterListItem(BaseModel):
    ordinal: int
    index: int
    kind: ChapterKindName
    chapter_number: int | None = None
    title: str
    title_source: str
    status: str
    chunk_first: int
    chunk_last: int
    duration_ms: int | None = None
    size_bytes: int | None = None
    error_type: str | None = None
    message: str | None = None
    filename: str
    download_url: str | None = None
    tts_engine: str | None = None
    note: str | None = None


class ChapterListResponse(BaseModel):
    job_id: str
    total: int
    ready: int
    failed: int
    matched: int
    offset: int
    limit: int
    partial: bool
    items: list[ChapterListItem]


class TTSPreviewRequest(BaseModel):
    text: str
    voice: str
    speed: float = 1.0
    tts_engine: str | None = None


class TTSPreviewResponse(BaseModel):
    audio_base64: str
    mime_type: str = "audio/mpeg"
