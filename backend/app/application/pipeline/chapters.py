import re
from dataclasses import dataclass

from app.application.pipeline.chunk import chunk_source
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk

__all__ = ["Chapter", "chunk_by_chapter", "is_chapter_heading", "split_chapters"]

_MAX_HEADING_LEN = 60
_DELIMITER_CHARS = r"\s:：、.·\-_/\\–—「」『』【】《》〈〉()（）\[\]"
_DELIM_PATTERN = rf"[\s{_DELIMITER_CHARS}]"

_ZH_HEAD = re.compile(
    r"^[ \t]*第\s*[0-9０-９零一二三四五六七八九十百千万两]+\s*(?:章|回(?!合)|节(?!课)|卷(?!入))",
    re.MULTILINE,
)
_VI_HEAD = re.compile(
    r"^[ \t]*(?:Chương|Hồi)\s+(?:\d+|[IVXLCDM]+)",
    re.MULTILINE | re.IGNORECASE,
)
_EN_HEAD = re.compile(
    r"^[ \t]*(?:Chapter|Book|Act)\s+(?:\d+|[IVXLCDM]+)",
    re.MULTILINE | re.IGNORECASE,
)


def is_chapter_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_LEN:
        return False
    # Clause / dialogue punctuation disqualifies a line from being a chapter title
    if any(p in stripped for p in (",", "，", ";", "；", '"', "“", "”")):
        return False
    if stripped.endswith((".", "。")):
        return False

    # 1. Chinese headings
    m_zh = _ZH_HEAD.match(stripped)
    if m_zh:
        after = stripped[m_zh.end() :]
        if not after:  # End of line, e.g. 第十二章
            return True
        if re.match(_DELIM_PATTERN, after):  # e.g. 第1章「开端」, 第一章 天才, 第3章【突破】
            return True
        # No-space titles, e.g. 第一章天才少年: must be short and contain no sentence punctuation
        if len(stripped) <= 35 and not any(p in stripped for p in ("。", "！", "？", "!", "?")):
            return True

    # 2. Vietnamese headings
    m_vi = _VI_HEAD.match(stripped)
    if m_vi:
        after = stripped[m_vi.end() :]
        if not after or re.match(_DELIM_PATTERN, after):
            return True

    # 3. English headings
    m_en = _EN_HEAD.match(stripped)
    if m_en:
        after = stripped[m_en.end() :]
        if not after or re.match(_DELIM_PATTERN, after):
            return True

    return False


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    body: str
    char_count: int


def split_chapters(text: str) -> list[Chapter]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    headings: list[tuple[int, int, str]] = []
    line_start = 0
    for line in normalized.splitlines(keepends=True):
        line_len = len(line)
        content = line.rstrip("\r\n")
        if is_chapter_heading(content):
            end_idx = line_start + len(content)
            headings.append((line_start, end_idx, content.strip()))
        line_start += line_len

    if not headings:
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
    first_start = headings[0][0]
    preface = normalized[:first_start].strip()
    if preface:
        chapters.append(
            Chapter(
                index=0,
                title="",
                body=preface,
                char_count=len(preface),
            )
        )

    for i, (h_start, h_end, title) in enumerate(headings, start=1):
        body_start = h_end
        body_end = headings[i][0] if i < len(headings) else len(normalized)
        body = normalized[body_start:body_end].strip()

        chapters.append(
            Chapter(
                index=i,
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
