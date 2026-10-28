import re
from dataclasses import dataclass

from app.application.pipeline.chunk import chunk_source
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk

__all__ = ["Chapter", "chunk_by_chapter", "is_chapter_heading", "split_chapters"]

_MAX_HEADING_LEN = 60
_DELIMITER_CHARS = r"\s:：、.·\-_/\\–—「」『』【】《》〈〉()（）\[\]"
_DELIM_PATTERN = rf"[\s{_DELIMITER_CHARS}]"

_ZH_HEAD = re.compile(
    r"^[ \t]*第\s*[0-9０-９零一二三四五六七八九十百千万两]+\s*"
    r"(?:(章|回(?!合)|节(?!课))|(卷(?!入)))",
    re.MULTILINE,
)
_VI_HEAD = re.compile(
    r"^[ \t]*(?:(Chương|Hồi)|(Quyển|Tập))\s+(?:\d+|[IVXLCDM]+)",
    re.MULTILINE | re.IGNORECASE,
)
_EN_HEAD = re.compile(
    r"^[ \t]*(?:(Chapter|Act)|(Book|Volume))\s+(?:\d+|[IVXLCDM]+)",
    re.MULTILINE | re.IGNORECASE,
)

_VOL_HEAD = re.compile(
    r"^[ \t]*(?:"
    r"第\s*[0-9０-９零一二三四五六七八九十百千万两]+\s*卷(?!入)|"
    r"(?:Book|Volume)\s+(?:\d+|[IVXLCDM]+)|"
    r"(?:Quyển|Tập)\s+(?:\d+|[IVXLCDM]+)"
    r")",
    re.MULTILINE | re.IGNORECASE,
)


def is_chapter_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_LEN:
        return False
    # Entire line ending in sentence-ending punctuation is prose
    if stripped.endswith((".", "。")):
        return False

    # 1. Chinese headings
    m_zh = _ZH_HEAD.match(stripped)
    if m_zh:
        unit = m_zh.group(1) or ("卷" if m_zh.group(2) else "")
        after = stripped[m_zh.end() :]
        if not after:  # End of line, e.g. 第十二章
            return True
        if re.match(r"^[:：、\-_/\\–—「」『』【】《》〈〉()（）\[\]]", after):
            # Non-space delimiter: e.g. 第1章「开端」, 第3章【突破】, 第一章：开端
            return True
        if after[0] in " \t":
            # Space + title, e.g. 第一章 天才, 第一回 启程
            rest = after.strip()
            if not rest:
                return True
            if any(p in rest for p in (",", "，", ";", "；", '"', "“", "”")):
                return False
            return True
        # No-space titles, e.g. 第一章天才少年: only for '章', must be short with no punctuation
        if unit.startswith("章") and len(stripped) <= 35:
            sentence_punct = (",", "，", ";", "；", "。", "！", "？", "!", "?", '"', "“", "”")
            if not any(p in stripped for p in sentence_punct):
                return True

    # 2. Vietnamese headings
    m_vi = _VI_HEAD.match(stripped)
    if m_vi:
        after = stripped[m_vi.end() :]
        if not after:
            return True
        # Non-space delimiter, e.g. Chương 1: Mở đầu, Chương 5: Gió, mưa và lửa
        if re.match(r"^[ \t]*[:：、.·\-_/\\–—「」『』【】《》〈〉()（）\[\]]", after):
            return True
        # Space + free text, e.g. Chương 1 Mở đầu
        if after[0] in " \t":
            rest = after.strip()
            if not rest:
                return True
            # Title must start with capital letter and not contain clause/dialogue punctuation
            # Disqualifies prose like "Hồi 3 tuổi tôi ở quê" or "Hồi 3 tuổi, tôi ở quê."
            clause_punct = (",", "，", ";", "；", '"', "“", "”")
            if rest[0].isupper() and not any(p in rest for p in clause_punct):
                first_word = rest.split()[0].lower()
                if first_word not in {"tuổi", "đó", "nhỏ", "xưa", "trước", "năm"}:
                    return True

    # 3. English headings
    m_en = _EN_HEAD.match(stripped)
    if m_en:
        after = stripped[m_en.end() :]
        if not after:
            return True
        # Non-space delimiter, e.g. Chapter 1: Hello, World or Chapter 3: "The Return"
        if re.match(r"^[ \t]*[:：.·\-_/\\–—\"'()\[\]]", after):
            return True
        # Space + free text, e.g. Chapter 1 The Beginning
        if after[0] in " \t":
            rest = after.strip()
            if not rest:
                return True
            # Must start with capital letter and not contain clause punctuation
            # Disqualifies prose like "Chapter 3 was the best part of the"
            if rest[0].isupper() and not any(p in rest for p in (",", ";", '"', "“", "”")):
                first_word = rest.split()[0].lower()
                if first_word not in {
                    "was",
                    "were",
                    "is",
                    "are",
                    "had",
                    "has",
                    "said",
                    "told",
                    "became",
                    "seemed",
                    "he",
                    "she",
                    "they",
                    "we",
                    "it",
                }:
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

    # When text has numbered chapters, volume/book headings are divisions with index=0
    has_chapter_headings = any(not _VOL_HEAD.match(title) for _, _, title in headings)

    chapter_counter = 1
    for i, (h_start, h_end, title) in enumerate(headings):
        body_start = h_end
        body_end = headings[i + 1][0] if i + 1 < len(headings) else len(normalized)
        body = normalized[body_start:body_end].strip()

        is_volume = bool(_VOL_HEAD.match(title)) and has_chapter_headings
        if is_volume:
            idx = 0
        else:
            idx = chapter_counter
            chapter_counter += 1

        chapters.append(
            Chapter(
                index=idx,
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
        title = chapter.title.strip()
        if not body:
            # Preserve heading in narration even when body is empty (e.g. volume headings)
            if title and title != "Full text":
                text_to_chunk = title
            else:
                continue
        elif title and title != "Full text":
            text_to_chunk = f"{title}\n\n{body}"
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
