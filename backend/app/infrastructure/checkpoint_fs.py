from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from app.domain.errors import DomainError, ErrorType

CHECKPOINT_FILENAME = "checkpoint.json"
CHECKPOINT_JSONL_FILENAME = "checkpoint.jsonl"


class CheckpointFilesystem:
    """Append-only ``checkpoint.jsonl`` with a one-time read of legacy ``checkpoint.json``.

    Skip policy lives in application. ``save_raw`` rewrites a compact JSONL file.
    ``append_raw`` adds one record. A legacy JSON list is migrated on read and
    removed when the compact rewrite succeeds.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._file = workspace / CHECKPOINT_FILENAME
        self._jsonl = workspace / CHECKPOINT_JSONL_FILENAME

    def load_raw(self) -> list[Any]:
        if self._jsonl.is_file():
            return self._read_jsonl(self._jsonl)
        if self._file.is_file():
            entries = self._read_legacy(self._file)
            self._write_jsonl_atomic(entries)
            return entries
        return []

    def append_raw(self, entry: dict[str, str]) -> None:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            self._workspace.mkdir(parents=True, exist_ok=True)
            with self._jsonl.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write checkpoint") from exc

    def save_raw(self, entries: list[dict[str, str]]) -> None:
        try:
            self._workspace.mkdir(parents=True, exist_ok=True)
            self._write_jsonl_atomic(entries)
            self._file.unlink(missing_ok=True)
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write checkpoint") from exc

    def has_legacy(self) -> bool:
        return self._file.is_file()

    def line_count(self) -> int:
        if not self._jsonl.is_file():
            return 0
        try:
            text = self._jsonl.read_text(encoding="utf-8")
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
        return sum(1 for line in text.splitlines() if line.strip())

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

    def _read_legacy(self, path: Path) -> list[Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
        if not isinstance(raw, list):
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint")
        return raw

    def _read_jsonl(self, path: Path) -> list[Any]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
        entries: list[Any] = []
        invalid = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if not isinstance(item, dict):
                invalid += 1
                continue
            entries.append(item)
        if invalid and not entries:
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint")
        return entries

    def _write_jsonl_atomic(self, entries: list[Any]) -> None:
        lines = [json.dumps(entry, ensure_ascii=False, separators=(",", ":")) for entry in entries]
        payload = ("\n".join(lines) + "\n") if lines else ""
        tmp = self._jsonl.with_name(f".{CHECKPOINT_JSONL_FILENAME}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self._jsonl)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
