import re
from dataclasses import dataclass

from app.application.pipeline.chunk import chunk_source
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk

__all__ = ["Chapter", "chunk_by_chapter", "split_chapters"]

_CHAPTER_HEADING_PATTERN = re.compile(
    r"^\s*(?:"
    r"第\s*[0-9零一二三四五六七八九十百千万两]+\s*[章回节卷]|"
    r"(?:Chương|Hồi)\s+(?:\d+|[IVXLCDM]+)\b|"
    r"(?:Chapter|Book|Act)\s+(?:\d+|[IVXLCDM]+)\b"
    r").*",
    re.MULTILINE | re.IGNORECASE,
)


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    body: str
    char_count: int


def split_chapters(text: str) -> list[Chapter]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    matches = list(_CHAPTER_HEADING_PATTERN.finditer(normalized))
    if len(matches) < 2:
        stripped = normalized.strip()
        return [
            Chapter(
                index=1,
                title="Full text",
                body=stripped,
                char_count=len(stripped),
            )
        ]

    chapters: list[Chapter] = []
    preface = normalized[: matches[0].start()].strip()

    for i, match in enumerate(matches):
        title = match.group(0).strip()
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(normalized)
        body = normalized[body_start:body_end].strip()

        if i == 0 and preface:
            body = f"{preface}\n\n{body}".strip() if body else preface

        chapters.append(
            Chapter(
                index=i + 1,
                title=title,
                body=body,
                char_count=len(body),
            )
        )

    return chapters


def chunk_by_chapter(chapters: list[Chapter], max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    chunks: list[Chunk] = []
    global_index = 1
    for chapter in chapters:
        body = chapter.body.strip()
        if not body:
            continue
        chapter_chunks = chunk_source(body, max_chars=max_chars)
        for c in chapter_chunks:
            chunks.append(
                Chunk(
                    id=f"chunk-{global_index:03d}",
                    text=c.text,
                    index=global_index,
                )
            )
            global_index += 1
    return chunks
