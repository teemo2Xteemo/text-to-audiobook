import re
from dataclasses import dataclass

from app.application.pipeline.chunk import chunk_source
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk

__all__ = ["Chapter", "chunk_by_chapter", "split_chapters"]

_MAX_HEADING_LEN = 60
_DELIMITER_OR_END = r"(?=[\s:：、，,。.·!！?？\-_/\\()（）\[\]]|$)"

_CHAPTER_HEADING_PATTERN = re.compile(
    r"^[ \t]*(?:"
    r"第\s*[0-9０-９零一二三四五六七八九十百千万两]+\s*[章回节卷]" + _DELIMITER_OR_END + r"|"
    r"(?:Chương|Hồi)\s+(?:\d+|[IVXLCDM]+)" + _DELIMITER_OR_END + r"|"
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
    raw_matches = list(_CHAPTER_HEADING_PATTERN.finditer(normalized))
    matches = [m for m in raw_matches if len(m.group(0).strip()) <= _MAX_HEADING_LEN]

    if not matches:
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
    next_index = 1
    if preface:
        chapters.append(
            Chapter(
                index=next_index,
                title="",
                body=preface,
                char_count=len(preface),
            )
        )
        next_index += 1

    for i, match in enumerate(matches):
        title = match.group(0).strip()
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(normalized)
        body = normalized[body_start:body_end].strip()

        chapters.append(
            Chapter(
                index=next_index,
                title=title,
                body=body,
                char_count=len(body),
            )
        )
        next_index += 1

    return chapters


def chunk_by_chapter(chapters: list[Chapter], max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    chunks: list[Chunk] = []
    global_index = 1
    for chapter in chapters:
        body = chapter.body.strip()
        if not body:
            continue
        if chapter.title and chapter.title != "Full text":
            text_to_chunk = f"{chapter.title}\n\n{body}"
        else:
            text_to_chunk = body
        chapter_chunks = chunk_source(text_to_chunk, max_chars=max_chars)
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
