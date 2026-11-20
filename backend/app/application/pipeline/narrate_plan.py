"""Persist the narrate chunk plan so a resume keeps the split it started with.

A detector change must not assign a completed ``chunk-005`` to different text.
The plan is written once, on the first split, and reused until the job ends.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.application.pipeline.chapters import ChapterChunkSpan, plan_chapter_chunks, split_chapters
from app.application.pipeline.parse import prepare_narration_text, strip_srt
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk

logger = logging.getLogger(__name__)

PLAN_FILENAME = "narrate-plan.json"
_CHUNK_ID = re.compile(r"^chunk-\d+$")


def plan_path(workspace: Path) -> Path:
    return workspace / "chunks" / PLAN_FILENAME


def prepared_narration_text(text: str) -> str:
    """Text `_run_narrate` chunks: newline normalize, subtitle strip, then cleanup."""
    normalized = strip_srt(text.replace("\r\n", "\n").replace("\r", "\n").strip())
    return prepare_narration_text(normalized)


def plan_narration_chunks(
    text: str, *, max_chars: int = CHUNK_MAX_CHARS
) -> tuple[list[Chunk], list[ChapterChunkSpan]]:
    """Chunk narrate input. A saved plan, not a later call, is what resume uses."""
    return plan_chapter_chunks(split_chapters(prepared_narration_text(text)), max_chars=max_chars)


def save_narrate_plan(workspace: Path, chunks: list[Chunk]) -> None:
    payload = {
        "version": 1,
        "chunks": [
            {
                "id": chunk.id,
                "index": chunk.index,
                "chapter": chunk.chapter,
                "text": chunk.text,
            }
            for chunk in chunks
        ],
    }
    path = plan_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _warn_unreadable(path: Path, reason: str) -> None:
    logger.warning(
        "narrate_plan_unreadable",
        extra={"path": path.name, "reason": reason},
    )


def load_narrate_plan(workspace: Path) -> list[Chunk] | None:
    path = plan_path(workspace)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw["chunks"]
        if not isinstance(items, list) or not items:
            _warn_unreadable(path, "empty")
            return None
        chunks: list[Chunk] = []
        for item in items:
            chunk_id = str(item["id"])
            if _CHUNK_ID.fullmatch(chunk_id) is None:
                _warn_unreadable(path, "invalid-id")
                return None
            chunks.append(
                Chunk(
                    id=chunk_id,
                    text=str(item["text"]),
                    index=int(item["index"]),
                    chapter=int(item.get("chapter", 0)),
                )
            )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        _warn_unreadable(path, type(exc).__name__)
        return None
    return chunks
