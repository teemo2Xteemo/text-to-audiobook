"""Per-chapter manifest records. Kept off ``Job`` so status ticks stay small."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

_CONTROL = re.compile(r"[\x00-\x1f]")
_RESERVED_CHARS = re.compile(r'[\\/:*?"<>|]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_TITLE_CHARS = 80
_TITLE_BYTES = 150

_ALLOWED: dict[str, frozenset[str]] = {
    "pending": frozenset({"generating"}),
    "generating": frozenset({"ready", "failed"}),
    "ready": frozenset({"generating"}),
    "failed": frozenset({"generating"}),
}


class ChapterStatus(StrEnum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


def ensure_chapter_transition(current: ChapterStatus, new: ChapterStatus) -> None:
    if new.value not in _ALLOWED[current.value]:
        raise ValueError(f"illegal chapter transition {current.value} -> {new.value}")


def chapter_checkpoint_id(ordinal: int) -> str:
    return f"chapter-{ordinal:04d}"


def chapter_disk_name(ordinal: int, ext: str) -> str:
    """ASCII relative path. The ordinal is the unique span id, not the printed number."""
    return f"chapters/{ordinal:04d}.{ext}"


def sanitize_title(title: str, ordinal: int) -> str:
    text = unicodedata.normalize("NFC", title or "")
    text = _CONTROL.sub("", text)
    text = _RESERVED_CHARS.sub(" ", text)
    text = " ".join(text.split()).strip(" .")
    stem = text.split(".", 1)[0].upper()
    if not text or stem in _WINDOWS_RESERVED:
        text = f"Chapter {ordinal}"
    text = text[:_TITLE_CHARS].strip(" .")
    while text and len(text.encode("utf-8")) > _TITLE_BYTES:
        text = text[:-1]
    text = text.strip(" .")
    return text or f"Chapter {ordinal}"


def display_filename(ordinal: int, title: str, ext: str, *, total: int | None = None) -> str:
    width = 4 if total is None else max(4, len(str(total)))
    safe = sanitize_title(title, ordinal)
    return f"{ordinal:0{width}d} - {safe}.{ext}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class ChapterRecord:
    ordinal: int
    kind: str
    chapter_number: int | None
    title_source: str
    title: str
    chunk_first: int
    chunk_last: int
    status: ChapterStatus = ChapterStatus.PENDING
    generation: int = 1
    file: str | None = None
    duration_ms: int | None = None
    size_bytes: int | None = None
    error_type: str | None = None
    message: str | None = None
    attempts: int = 0
    updated_at: str = field(default_factory=_now)

    def mark(self, status: ChapterStatus) -> None:
        ensure_chapter_transition(self.status, status)
        self.status = status
        self.updated_at = _now()

    def to_dict(self) -> dict[str, object]:
        return {
            "ordinal": self.ordinal,
            "kind": self.kind,
            "chapter_number": self.chapter_number,
            "title_source": self.title_source,
            "title": self.title,
            "chunk_first": self.chunk_first,
            "chunk_last": self.chunk_last,
            "status": self.status.value,
            "generation": self.generation,
            "file": self.file,
            "duration_ms": self.duration_ms,
            "size_bytes": self.size_bytes,
            "error_type": self.error_type,
            "message": self.message,
            "attempts": self.attempts,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ChapterRecord:
        kind = str(data["kind"])
        if kind not in {"preface", "volume", "chapter"}:
            raise ValueError("invalid chapter kind")
        status = ChapterStatus(str(data["status"]))
        number = data.get("chapter_number")
        return cls(
            ordinal=int(data["ordinal"]),  # type: ignore[arg-type]
            kind=kind,
            chapter_number=None if number is None else int(number),  # type: ignore[arg-type]
            title_source=str(data.get("title_source") or ""),
            title=str(data.get("title") or ""),
            chunk_first=int(data["chunk_first"]),  # type: ignore[arg-type]
            chunk_last=int(data["chunk_last"]),  # type: ignore[arg-type]
            status=status,
            generation=int(data.get("generation") or 1),  # type: ignore[arg-type]
            file=None if data.get("file") in (None, "") else str(data.get("file")),
            duration_ms=_optional_int(data.get("duration_ms")),
            size_bytes=_optional_int(data.get("size_bytes")),
            error_type=_optional_str(data.get("error_type")),
            message=_optional_str(data.get("message")),
            attempts=int(data.get("attempts") or 0),  # type: ignore[arg-type]
            updated_at=str(data.get("updated_at") or ""),
        )


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)  # type: ignore[arg-type]


@dataclass
class ChapterManifest:
    version: int
    max_chars: int
    chapter_aware: bool
    detector: str
    chapters: list[ChapterRecord]

    def counts(self) -> tuple[int, int, int]:
        ready = sum(1 for chapter in self.chapters if chapter.status is ChapterStatus.READY)
        failed = sum(1 for chapter in self.chapters if chapter.status is ChapterStatus.FAILED)
        return len(self.chapters), ready, failed

    def by_ordinal(self, ordinal: int) -> ChapterRecord | None:
        for chapter in self.chapters:
            if chapter.ordinal == ordinal:
                return chapter
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "chunking": {
                "max_chars": self.max_chars,
                "chapter_aware": self.chapter_aware,
                "detector": self.detector,
            },
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ChapterManifest:
        version = int(data["version"])  # type: ignore[arg-type]
        if version != 1:
            raise ValueError("unsupported chapter manifest version")
        chunking = data.get("chunking")
        if not isinstance(chunking, dict):
            raise ValueError("chapter manifest chunking missing")
        raw_chapters = data.get("chapters")
        if not isinstance(raw_chapters, list):
            raise ValueError("chapter manifest chapters missing")
        chapters = [
            ChapterRecord.from_dict(item) for item in raw_chapters if isinstance(item, dict)
        ]
        if len(chapters) != len(raw_chapters):
            raise ValueError("chapter manifest entry is not an object")
        return cls(
            version=version,
            max_chars=int(chunking["max_chars"]),  # type: ignore[arg-type]
            chapter_aware=bool(chunking.get("chapter_aware", True)),
            detector=str(chunking.get("detector") or ""),
            chapters=chapters,
        )
