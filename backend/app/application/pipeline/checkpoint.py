from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.domain.errors import DomainError, ErrorType
from app.domain.ports import CheckpointStorage

CHECKPOINT_FILENAME = "checkpoint.json"
STAGE_TRANSLATED = "translated"
STAGE_NARRATED = "narrated"
STAGE_TTS = "tts"
STAGE_NORMALIZED = "normalized"
STAGE_CHAPTER = "chapter"

__all__ = [
    "CHECKPOINT_FILENAME",
    "STAGE_CHAPTER",
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
    """In-memory index of checkpoint entries. Filesystem writes append one line."""

    def __init__(self, *, storage: CheckpointStorage) -> None:
        self._fs: CheckpointStorage = storage
        self._index: dict[tuple[str, str], CheckpointEntry] | None = None

    def is_complete(self, chunk_id: str, stage: str) -> bool:
        entry = self._index_of().get((chunk_id, stage))
        if entry is None:
            return False
        path = self._fs.resolve(entry.artifact_path)
        return path.is_file() and path.stat().st_size > 0

    def forget_chunk(self, chunk_id: str) -> None:
        """Drop every stage for ``chunk_id`` so a changed split cannot reuse it."""
        self.drop(chunk_ids={chunk_id})

    def drop(self, *, chunk_ids: set[str], stages: set[str] | None = None) -> None:
        """Remove selected rows. ``stages`` None removes every stage for those ids."""
        if not chunk_ids:
            return
        index = self._index_of()
        keys = [
            key for key in index if key[0] in chunk_ids and (stages is None or key[1] in stages)
        ]
        if not keys:
            return
        removed = [(key, index.pop(key)) for key in keys]
        try:
            self._fs.save_raw([item.to_dict() for item in index.values()])
        except Exception:
            for key, entry in removed:
                index[key] = entry
            raise

    def record(self, chunk_id: str, stage: str, artifact: Path) -> None:
        index = self._index_of()
        key = (chunk_id, stage)
        previous = index.get(key)
        entry = CheckpointEntry(
            chunk_id=chunk_id,
            stage=stage,
            artifact_path=self._fs.relative(artifact),
        )
        index[key] = entry
        try:
            append = getattr(self._fs, "append_raw", None)
            if callable(append):
                append(entry.to_dict())
            else:
                self._fs.save_raw([item.to_dict() for item in index.values()])
        except Exception:
            if previous is None:
                index.pop(key, None)
            else:
                index[key] = previous
            raise

    def load(self) -> list[CheckpointEntry]:
        return list(self._index_of().values())

    def entry_count(self) -> int:
        return len(self._index_of())

    def compact(self) -> None:
        """Rewrite one line per chunk-stage, and mirror that snapshot to ``checkpoint.json``.

        The mirror is how a rollback to a build that only reads ``checkpoint.json``
        keeps already finished chunks. It is refreshed when the log has duplicate
        lines or the mirror is missing, not on every append.
        """
        index = self._index_of()
        legacy = getattr(self._fs, "has_legacy", None)
        lines = getattr(self._fs, "line_count", None)
        duplicate_log = callable(lines) and lines() != len(index)
        missing_mirror = callable(legacy) and not legacy() and bool(index)
        if not duplicate_log and not missing_mirror:
            return
        self._fs.save_raw([item.to_dict() for item in index.values()])

    def _index_of(self) -> dict[tuple[str, str], CheckpointEntry]:
        if self._index is None:
            index: dict[tuple[str, str], CheckpointEntry] = {}
            try:
                raw_entries = self._fs.load_raw()
            except (KeyError, TypeError) as exc:
                raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
            try:
                for item in raw_entries:
                    entry = CheckpointEntry.from_dict(item)
                    index[(entry.chunk_id, entry.stage)] = entry
            except (KeyError, TypeError) as exc:
                raise DomainError(ErrorType.STORAGE_FAILED, "failed to read checkpoint") from exc
            self._index = index
        return self._index
