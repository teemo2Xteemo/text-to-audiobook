from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.errors import DomainError, ErrorType

CHECKPOINT_FILENAME = "checkpoint.json"


class CheckpointFilesystem:
    """JSON read/write for ``checkpoint.json``. Skip policy lives in application."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._file = workspace / CHECKPOINT_FILENAME

    def load_raw(self) -> list[Any]:
        if not self._file.is_file():
            return []
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
        if not isinstance(raw, list):
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint")
        return raw

    def save_raw(self, entries: list[dict[str, str]]) -> None:
        payload = json.dumps(entries, ensure_ascii=False, indent=2)
        tmp = self._file.with_suffix(".json.tmp")
        try:
            self._workspace.mkdir(parents=True, exist_ok=True)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._file)
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write checkpoint") from exc

    def relative(self, artifact: Path) -> str:
        resolved = artifact.resolve()
        workspace = self._workspace.resolve()
        if not resolved.is_relative_to(workspace):
            raise DomainError(ErrorType.STORAGE_FAILED, "invalid checkpoint path")
        return resolved.relative_to(workspace).as_posix()

    def resolve(self, artifact_path: str) -> Path:
        raw = Path(artifact_path)
        if not raw.is_absolute() and ".." in raw.parts:
            raise DomainError(ErrorType.STORAGE_FAILED, "invalid checkpoint path")
        resolved = raw.resolve() if raw.is_absolute() else (self._workspace / raw).resolve()
        if not resolved.is_relative_to(self._workspace.resolve()):
            raise DomainError(ErrorType.STORAGE_FAILED, "invalid checkpoint path")
        return resolved
