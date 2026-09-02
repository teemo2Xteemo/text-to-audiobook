from dataclasses import dataclass
from pathlib import Path

from app.domain.errors import DomainError, ErrorType

SPEED_DEFAULT = 1.0
SPEED_MAX = 2.0
SPEED_MIN = 0.5


@dataclass(frozen=True)
class TTSSettings:
    speed: float = SPEED_DEFAULT


def ensure_valid_speed(speed: float) -> None:
    if speed < SPEED_MIN or speed > SPEED_MAX:
        raise DomainError(
            ErrorType.INVALID_INPUT,
            f"speed must be between {SPEED_MIN} and {SPEED_MAX}",
        )


@dataclass(frozen=True)
class Voice:
    id: str
    language: str
    label: str


@dataclass(frozen=True)
class AudioArtifact:
    path: Path
