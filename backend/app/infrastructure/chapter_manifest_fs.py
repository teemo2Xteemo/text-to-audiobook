"""Atomic ``chapters.json`` for one job workspace. The worker is the only writer."""

from __future__ import annotations

import json
import logging
import uuid
from collections import OrderedDict
from pathlib import Path

from app.domain.chapters import ChapterManifest
from app.domain.errors import DomainError, ErrorType

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "chapters.json"
_CACHE_LIMIT = 128
# (mtime_ns, size, manifest). Status polls reuse this until the worker replaces the file.
_CACHE: OrderedDict[str, tuple[int, int, ChapterManifest | None]] = OrderedDict()


class FilesystemChapterManifest:
    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._path = workspace / MANIFEST_FILENAME

    def load(self) -> ChapterManifest | None:
        key = str(self._path)
        if not self._path.is_file():
            _CACHE.pop(key, None)
            return None
        try:
            stat = self._path.stat()
        except OSError as exc:
            logger.warning(
                "chapter_manifest_unreadable",
                extra={"path": self._path.name, "reason": type(exc).__name__},
            )
            return None
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            _CACHE.move_to_end(key)
            return cached[2]
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("manifest is not an object")
            manifest = ChapterManifest.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "chapter_manifest_unreadable",
                extra={"path": self._path.name, "reason": type(exc).__name__},
            )
            return None
        _remember(key, stat.st_mtime_ns, stat.st_size, manifest)
        return manifest

    def save(self, manifest: ChapterManifest) -> None:
        self._workspace.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(manifest.to_dict(), ensure_ascii=False, separators=(",", ":"))
        temporary = self._path.with_name(f".{MANIFEST_FILENAME}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self._path)
            stat = self._path.stat()
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            _CACHE.pop(str(self._path), None)
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write chapter manifest") from exc
        _remember(str(self._path), stat.st_mtime_ns, stat.st_size, manifest)


def _remember(key: str, mtime_ns: int, size: int, manifest: ChapterManifest | None) -> None:
    _CACHE[key] = (mtime_ns, size, manifest)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_LIMIT:
        _CACHE.popitem(last=False)
