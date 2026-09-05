from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.domain.errors import DomainError, ErrorType

CHECKPOINT_FILENAME = "checkpoint.json"
STAGE_TRANSLATED = "translated"
STAGE_NARRATED = "narrated"
STAGE_TTS = "tts"
STAGE_NORMALIZED = "normalized"


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
        self._workspace = workspace
        self._file = workspace / CHECKPOINT_FILENAME

    def is_complete(self, chunk_id: str, stage: str) -> bool:
        entry = self._find(chunk_id, stage)
        if entry is None:
            return False
        path = self._resolve(entry.artifact_path)
        return path.is_file() and path.stat().st_size > 0

    def record(self, chunk_id: str, stage: str, artifact: Path) -> None:
        relative = self._relative(artifact)
        entries = [
            item for item in self.load() if not (item.chunk_id == chunk_id and item.stage == stage)
        ]
        entries.append(CheckpointEntry(chunk_id=chunk_id, stage=stage, artifact_path=relative))
        self._write(entries)

    def load(self) -> list[CheckpointEntry]:
        if not self._file.is_file():
            return []
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
        if not isinstance(raw, list):
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint")
        try:
            return [CheckpointEntry.from_dict(item) for item in raw]
        except (KeyError, TypeError) as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc

    def _find(self, chunk_id: str, stage: str) -> CheckpointEntry | None:
        for entry in reversed(self.load()):
            if entry.chunk_id == chunk_id and entry.stage == stage:
                return entry
        return None

    def _write(self, entries: list[CheckpointEntry]) -> None:
        payload = json.dumps([entry.to_dict() for entry in entries], ensure_ascii=False, indent=2)
        tmp = self._file.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._file)
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write checkpoint") from exc

    def _relative(self, artifact: Path) -> str:
        resolved = artifact.resolve()
        workspace = self._workspace.resolve()
        if not resolved.is_relative_to(workspace):
            raise DomainError(ErrorType.STORAGE_FAILED, "invalid checkpoint path")
        return resolved.relative_to(workspace).as_posix()

    def _resolve(self, artifact_path: str) -> Path:
        raw = Path(artifact_path)
        if not raw.is_absolute() and ".." in raw.parts:
            raise DomainError(ErrorType.STORAGE_FAILED, "invalid checkpoint path")
        resolved = raw.resolve() if raw.is_absolute() else (self._workspace / raw).resolve()
        if not resolved.is_relative_to(self._workspace.resolve()):
            raise DomainError(ErrorType.STORAGE_FAILED, "invalid checkpoint path")
        return resolved
