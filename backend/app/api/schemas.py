from __future__ import annotations

from pydantic import BaseModel, Field, model_serializer

from app.domain.audio import SPEED_DEFAULT, Voice
from app.domain.jobs import Job, JobStatus, OutputFormat


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
        )


class CreateJobJsonBody(BaseModel):
    text: str
    source_language: str
    target_language: str
    voice: str | None = None
    speed: float = SPEED_DEFAULT
    output_format: str = Field(default=OutputFormat.MP3.value)
    translation_profile_id: str | None = None


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


class CapabilitiesResponse(BaseModel):
    languages: list[str]
    voices: list[VoiceResponse]
    translation_profiles: list[TranslationProfileResponse] = Field(default_factory=list)
