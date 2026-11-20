"""Atomic ``chapters.json`` for one job workspace. The worker is the only writer."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from app.domain.chapters import ChapterManifest
from app.domain.errors import DomainError, ErrorType

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "chapters.json"


class FilesystemChapterManifest:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._path = workspace / MANIFEST_FILENAME

    def load(self) -> ChapterManifest | None:
        if not self._path.is_file():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("manifest is not an object")
            return ChapterManifest.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "chapter_manifest_unreadable",
                extra={"path": self._path.name, "reason": type(exc).__name__},
            )
            return None

    def save(self, manifest: ChapterManifest) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
        temporary = self._path.with_name(f".{MANIFEST_FILENAME}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self._path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write chapter manifest") from exc
