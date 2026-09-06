from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.domain.audio import SPEED_DEFAULT
from app.domain.errors import ErrorType


class JobStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    TRANSLATING = "translating"
    PREPARING_TTS = "preparing_tts"
    GENERATING_AUDIO = "generating_audio"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


class IllegalJobTransition(Exception):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"illegal job transition: {current.value} -> {target.value}")


_FORWARD: dict[JobStatus, JobStatus] = {
    JobStatus.QUEUED: JobStatus.PARSING,
    JobStatus.PARSING: JobStatus.TRANSLATING,
    JobStatus.TRANSLATING: JobStatus.PREPARING_TTS,
    JobStatus.PREPARING_TTS: JobStatus.GENERATING_AUDIO,
    JobStatus.GENERATING_AUDIO: JobStatus.MERGING,
    JobStatus.MERGING: JobStatus.COMPLETED,
}

_PIPELINE: tuple[JobStatus, ...] = (
    JobStatus.QUEUED,
    JobStatus.PARSING,
    JobStatus.TRANSLATING,
    JobStatus.PREPARING_TTS,
    JobStatus.GENERATING_AUDIO,
    JobStatus.MERGING,
    JobStatus.COMPLETED,
)
_PIPELINE_INDEX = {status: index for index, status in enumerate(_PIPELINE)}
_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})


def is_terminal(status: JobStatus) -> bool:
    return status in _TERMINAL


def is_at_or_past(current: JobStatus, target: JobStatus) -> bool:
    if current is JobStatus.FAILED or target is JobStatus.FAILED:
        return False
    current_index = _PIPELINE_INDEX.get(current)
    target_index = _PIPELINE_INDEX.get(target)
    if current_index is None or target_index is None:
        return False
    return current_index >= target_index


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    if current is JobStatus.COMPLETED:
        return False
    if current is JobStatus.FAILED:
        return target is JobStatus.QUEUED
    if target is JobStatus.FAILED:
        return True
    return _FORWARD.get(current) is target


def assert_legal_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise IllegalJobTransition(current, target)


class OutputFormat(StrEnum):
    MP3 = "mp3"
    WAV = "wav"


@dataclass(frozen=True)
class Job:
    id: str
    status: JobStatus
    source_language: str
    target_language: str
    voice: str | None
    speed: float
    output_format: OutputFormat
    output_bitrate_kbps: int
    chunk_current: int = 0
    chunk_total: int = 0
    error_type: ErrorType | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_current": self.chunk_current,
            "chunk_total": self.chunk_total,
            "error_type": self.error_type.value if self.error_type is not None else None,
            "id": self.id,
            "message": self.message,
            "output_bitrate_kbps": self.output_bitrate_kbps,
            "output_format": self.output_format.value,
            "source_language": self.source_language,
            "speed": self.speed,
            "status": self.status.value,
            "target_language": self.target_language,
            "voice": self.voice,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Job:
        error_raw = data.get("error_type")
        message = data.get("message")
        voice = data.get("voice")
        return cls(
            id=str(data["id"]),
            status=JobStatus(str(data["status"])),
            source_language=str(data["source_language"]),
            target_language=str(data["target_language"]),
            voice=str(voice) if voice else None,
            speed=float(data.get("speed", SPEED_DEFAULT)),
            output_format=OutputFormat(str(data["output_format"])),
            output_bitrate_kbps=int(data["output_bitrate_kbps"]),
            chunk_current=int(data.get("chunk_current", 0)),
            chunk_total=int(data.get("chunk_total", 0)),
            error_type=ErrorType(str(error_raw)) if error_raw else None,
            message=str(message) if message else None,
        )
