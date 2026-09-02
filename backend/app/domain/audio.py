from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TTSSettings:
    speed: float = 1.0


@dataclass(frozen=True)
class Voice:
    id: str
    language: str
    label: str


@dataclass(frozen=True)
class AudioArtifact:
    path: Path
