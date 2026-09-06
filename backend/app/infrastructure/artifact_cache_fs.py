from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.domain.cache import CACHE_OPERATIONS
from app.domain.errors import DomainError, ErrorType

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class FilesystemArtifactCache:
    """Content-addressed blobs under ``{root}/{operation}/{sha256}``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def get(self, operation: str, key: str, destination: Path) -> bool:
        blob = self._blob(operation, key)
        if not blob.is_file() or blob.stat().st_size == 0:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blob, destination)
        return destination.is_file() and destination.stat().st_size > 0

    def put(self, operation: str, key: str, source: Path) -> None:
        if not source.is_file() or source.stat().st_size == 0:
            return
        destination = self._blob(operation, key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_name(f"{destination.name}.tmp")
        try:
            shutil.copyfile(source, tmp)
            tmp.replace(destination)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise DomainError(ErrorType.STORAGE_FAILED, "failed to write cache") from exc

    def _blob(self, operation: str, key: str) -> Path:
        if operation not in CACHE_OPERATIONS:
            raise DomainError(ErrorType.INVALID_INPUT, "unsupported cache operation")
        if _HEX64.fullmatch(key) is None:
            raise DomainError(ErrorType.INVALID_INPUT, "invalid cache key")
        root = (self._root / operation).resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise DomainError(ErrorType.INVALID_INPUT, "invalid cache key")
        return path
