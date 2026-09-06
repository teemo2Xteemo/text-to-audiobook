from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.errors import DomainError, ErrorType
from app.infrastructure.checkpoint_fs import CHECKPOINT_FILENAME, CheckpointFilesystem

STAGE_TRANSLATED = "translated"
STAGE_NARRATED = "narrated"
STAGE_TTS = "tts"
STAGE_NORMALIZED = "normalized"

__all__ = [
    "CHECKPOINT_FILENAME",
    "STAGE_NARRATED",
    "STAGE_NORMALIZED",
    "STAGE_TRANSLATED",
    "STAGE_TTS",
    "CheckpointEntry",
    "CheckpointStore",
]


@dataclass(frozen=True)
class CheckpointEntry:
    chunk_id: str
    stage: str
    artifact_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_path": self.artifact_path,
            "chunk_id": self.chunk_id,
            "stage": self.stage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CheckpointEntry:
        return cls(
            chunk_id=str(data["chunk_id"]),
            stage=str(data["stage"]),
            artifact_path=str(data["artifact_path"]),
        )


class CheckpointStore:
    def __init__(self, workspace: Path) -> None:
        self._fs = CheckpointFilesystem(workspace)

    def is_complete(self, chunk_id: str, stage: str) -> bool:
        entry = self._find(chunk_id, stage)
        if entry is None:
            return False
        path = self._fs.resolve(entry.artifact_path)
        return path.is_file() and path.stat().st_size > 0

    def record(self, chunk_id: str, stage: str, artifact: Path) -> None:
        relative = self._fs.relative(artifact)
        entries = [
            item for item in self.load() if not (item.chunk_id == chunk_id and item.stage == stage)
        ]
        entries.append(CheckpointEntry(chunk_id=chunk_id, stage=stage, artifact_path=relative))
        self._fs.save_raw([entry.to_dict() for entry in entries])

    def load(self) -> list[CheckpointEntry]:
        try:
            return [CheckpointEntry.from_dict(item) for item in self._fs.load_raw()]
        except (KeyError, TypeError) as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc

    def _find(self, chunk_id: str, stage: str) -> CheckpointEntry | None:
        for entry in reversed(self.load()):
            if entry.chunk_id == chunk_id and entry.stage == stage:
                return entry
        return None
