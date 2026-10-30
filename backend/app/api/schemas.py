from __future__ import annotations

from pydantic import BaseModel, Field, model_serializer

from app.domain.audio import SPEED_DEFAULT, SPEED_MAX, SPEED_MIN, Voice
from app.domain.jobs import Job, JobMode, JobStatus, OutputFormat, SrtTiming


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
    def from_job(cls, job: Job, *, subtitles_url: str | None = None) -> JobStatusResponse:
        audio_url = f"/api/jobs/{job.id}/audio" if job.status is JobStatus.COMPLETED else None
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


class VoiceResponse(BaseModel):
    id: str
    language: str
    label: str

    @classmethod
    def from_voice(cls, voice: Voice) -> VoiceResponse:
        return cls(id=voice.id, language=voice.language, label=voice.label)


class TranslationProfileResponse(BaseModel):
    id: str
    provider: str
    model: str
    label: str
    badge: str
    description: str
    is_default: bool
    supported_languages: list[str] = Field(default_factory=list)


class CapabilitiesResponse(BaseModel):
    languages: list[str]
    voices: list[VoiceResponse]
    translation_profiles: list[TranslationProfileResponse] = Field(default_factory=list)
    narrate_languages: list[str] = Field(default_factory=list)
    estimate_defaults: dict[str, float] = Field(default_factory=dict)


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


class ChapterSummary(BaseModel):
    index: int
    title: str
    char_count: int


class AnalyzeResponse(BaseModel):
    encoding: str
    language: str
    confidence: float
    needs_confirmation: bool
    candidates: list[LanguageCandidate]
    chapters_count: int
    chapters_preview: list[ChapterSummary]
    duration_estimate_seconds: float
    process_time_estimate_seconds: float
    preview_lines: list[str]


class TTSPreviewRequest(BaseModel):
    text: str
    voice: str
    speed: float = 1.0


class TTSPreviewResponse(BaseModel):
    audio_base64: str
    mime_type: str = "audio/mpeg"
