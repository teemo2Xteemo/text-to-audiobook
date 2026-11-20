"""Chapter splitting for narrate (and, later, per-chapter download).

Detection is two steps:

1. A permissive per-line candidate (prefix + number + optional title), with a
   small pronoun tie-breaker. This is what :func:`is_chapter_heading` reports.
2. A document pass inside :func:`split_chapters`: drop candidates that break
   the numbering sequence, that use a rare heading pattern, or that are as
   long as a hard-wrapped prose line. Weak evidence keeps fewer splits and
   lowers ``detection_confidence``.

There is no narrative-verb list. Kinship words (``anh/cô/ông/bà/lão/chị/cậu/y``)
count as pronouns only when the next word is ``ấy/ta/kia/này/nọ/mình``.
``chúng ta`` and ``chúng tôi`` are titles (CC1). ``chúng nó`` is prose.

Remaining trade-offs (kept on purpose, covered by the trade-off corpus):

- **T2.** Lowercase Roman numerals (``chapter iv``, ``Chapter iv: Hi``) are
  not headings. Uppercase Roman numerals are.
- **Y2.** A sentence-case title that opens with an unambiguous pronoun
  (``Chương 9: Hắn là thiên tài sao``) is rejected, so prose such as
  ``Hắn bước vào phòng vắng`` is not read aloud as a title.
- **Layout.** A heading as long as the book's wrap width, with no blank line
  around it and a sentence-case title of eight or more words, is treated as
  prose when the document has other headings.
- **T4** (``Chapter 2 The End.``, ``Chapter 1 Mr.``) is accepted: a short
  title after a space is not prose just because it ends with a period.

Span identity (S2), for #29 file names:

- ``ordinal`` and ``index`` are the same unique 1-based position of every
  span in the book (preface, volume, and chapter). They are not reused.
- ``kind`` is ``preface``, ``volume``, or ``chapter``.
- ``chapter_number`` is the number printed on a chapter heading (it may
  restart after a volume). It is ``None`` for prefaces, volumes, and the
  synthetic "Full text" span.
- A book made only of volume lines (``Book I`` / ``Book II``) promotes those
  lines to ``kind=chapter`` so they still count as the book's divisions.
- Preface and volume used to share ``index == 0``. That overload is gone.

Add a corpus row in ``backend/tests/fixtures/chapters/headings.json``
(see the file's ``about`` field). Run ``make chapters-eval`` for precision
and recall on the labelled book excerpts.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from app.application.pipeline.chunk import chunk_source
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk

__all__ = [
    "Chapter",
    "ChapterKind",
    "ChapterSplit",
    "chunk_by_chapter",
    "is_chapter_heading",
    "split_chapters",
    "split_chapters_detailed",
]

# Soft cap. The old hard stop at 60 dropped real titles (V2). Lines near the
# book's wrap width are judged again in the document pass.
_SOFT_MAX_HEADING_LEN = 100
_UNCERTAIN_BELOW = 0.8
_GLUED_ZHANG_MAX = 35

_DELIMS = set(":：、.·-_/\u2013\u2014「」『』【】《》〈〉()（）[]\"'")
_CLAUSE_PUNCT = set(',，;；"“”')
_SENTENCE_END = set("。！？")

_ZH_LINE = re.compile(
    r"^第\s*(?P<num>[0-9０-９零一二三四五六七八九十百千万两〇]+)\s*"
    r"(?P<unit>章|回(?!合)|节(?!课)|卷(?!入))"
    r"(?P<after>.*)$"
)
_VI_LINE = re.compile(
    r"^(?P<unit>(?i:Chương|Hồi|Quyển|Tập))\s+"
    r"(?P<num>\d+|[IVXLCDM]+)\b"
    r"(?P<after>.*)$"
)
_EN_LINE = re.compile(
    r"^(?P<unit>(?i:Chapter|Act|Book|Volume))\s+"
    r"(?P<num>\d+|[IVXLCDM]+)\b"
    r"(?P<after>.*)$"
)

_VI_STOPWORDS = {
    "tuổi",
    "đó",
    "nhỏ",
    "xưa",
    "trước",
    "năm",
    "khi",
    "lúc",
    "và",
    "nhưng",
}
_EN_STOPWORDS = {
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
    "and",
    "but",
    "so",
    "then",
}
_UNAMBIGUOUS_PRONOUNS = {"he", "she", "they", "i", "we", "it", "hắn", "nó", "ta", "tôi", "nàng"}
_KINSHIP_STARTERS = {"ông", "bà", "anh", "chị", "cô", "cậu", "lão", "y"}
_DEMONSTRATIVES = {"ấy", "ta", "kia", "này", "nọ", "mình"}
_WORD_PUNCT = "\"'“”‘’:;,.…—–-()[]<>"
_CN_DIGIT = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


class ChapterKind(StrEnum):
    PREFACE = "preface"
    VOLUME = "volume"
    CHAPTER = "chapter"


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    body: str
    char_count: int
    kind: ChapterKind = ChapterKind.CHAPTER
    ordinal: int = 0
    chapter_number: int | None = None


@dataclass(frozen=True)
class ChapterSplit:
    """Chapters plus the document-level detection score.

    ``detection_confidence`` is 1 when the text has no heading candidates.
    ``uncertain`` is true below 0.8 so the analyze panel can ask the user to
    check the list instead of trusting a silent split.
    """

    chapters: list[Chapter]
    detection_confidence: float
    uncertain: bool


@dataclass
class _Candidate:
    start: int
    end: int
    title: str
    lang: str
    role: str
    number: int | None
    title_text: str
    pattern_key: str
    blank_before: bool
    blank_after: bool
    line_len: int


def _norm_word(word: str) -> str:
    return word.strip(_WORD_PUNCT).casefold()


def _core_word(word: str) -> str:
    return word.strip(_WORD_PUNCT)


def _split_tail(after: str) -> tuple[str, str]:
    """Return ``(delimiter, title)`` for the text after the heading number."""
    index = 0
    while index < len(after) and after[index].isspace():
        index += 1
    rest = after[index:]
    if not rest:
        return "", ""
    if rest[0] in _DELIMS:
        return rest[0], rest[1:].lstrip()
    return "", rest


def _is_sentence_cased(words: list[str]) -> bool:
    alpha: list[str] = []
    for word in words:
        core = _core_word(word)
        if core and core[0].isalpha():
            alpha.append(core)
    if not alpha:
        return False
    lowered = sum(1 for word in alpha if word[0].islower())
    return lowered > len(alpha) / 2


def _is_strong_title(title: str) -> bool:
    words = [word for word in title.split() if _core_word(word)]
    alpha: list[str] = []
    for word in words:
        core = _core_word(word)
        if core and core[0].isalpha():
            alpha.append(core)
    if not alpha:
        return True
    upper = sum(1 for word in alpha if word[0].isupper())
    return upper * 2 >= len(alpha)


def _starts_lowercase_prose(title: str) -> bool:
    stripped = title.lstrip("\"'“”‘’«»")
    if not stripped or not stripped[0].islower():
        return False
    word = stripped.split(None, 1)[0]
    # "iPhone" / "eBay": a lowercase first letter with a capital later is a name.
    return not any(char.isupper() for char in word[1:])


def _pronoun_sentence(words: list[str]) -> bool:
    """True when the title is a sentence that merely starts with a pronoun.

    ``chúng ta`` / ``chúng tôi`` are common titles (CC1) and are not pronouns.
    ``chúng nó`` is prose. ``Bà ấy.`` / ``Anh ấy.`` with nothing after the
    marker are short titles (CC3). Title Case (``It Begins``, ``Hắn Là Ai``)
    is kept; sentence case with a real predicate is prose.
    """
    if not words:
        return False
    first = _norm_word(words[0])
    second = _norm_word(words[1]) if len(words) > 1 else ""
    if first == "chúng" and second in {"ta", "tôi"}:
        return False
    if first == "chúng" and second == "nó":
        return True
    if first in _UNAMBIGUOUS_PRONOUNS:
        if len(words) < 2 or not _is_sentence_cased(words[1:]):
            return False
        if len(words) >= 3:
            return True
        # "He left." is a two-word sentence. "It Ends." is title case and
        # already excluded above.
        last = words[-1].rstrip()
        return last.endswith(".") and not last.endswith("...")
    if first in _KINSHIP_STARTERS and second in _DEMONSTRATIVES:
        if len(words) <= 2:
            return False
        return _is_sentence_cased(words[1:])
    return False


def _tail_is_prose(title: str, *, lang: str, had_delimiter: bool) -> bool:
    if not title:
        return False
    if _starts_lowercase_prose(title):
        return True
    words = title.split()
    if _pronoun_sentence(words):
        return True
    if had_delimiter or not words:
        return False
    first = _norm_word(words[0])
    stopwords = _VI_STOPWORDS if lang == "vi" else _EN_STOPWORDS
    if first in stopwords:
        return True
    return any(char in title for char in _CLAUSE_PUNCT)


def _chinese_title_ok(line: str, unit: str, after: str) -> bool:
    if any(mark in line for mark in _SENTENCE_END):
        return False
    delim, title = _split_tail(after)
    if not after.strip():
        return True
    if delim:
        if delim in _CLAUSE_PUNCT:
            return False
        return not any(char in title for char in _CLAUSE_PUNCT)
    if after[:1].isspace():
        return not any(char in title for char in _CLAUSE_PUNCT)
    if unit != "章" or len(line) > _GLUED_ZHANG_MAX:
        return False
    glued_punct = _CLAUSE_PUNCT | _SENTENCE_END | set("!?")
    return not any(char in line for char in glued_punct)


def _parse_zh_number(raw: str) -> int | None:
    digits = raw.translate(_FULLWIDTH_DIGITS)
    if digits.isdigit():
        return int(digits)
    total = 0
    section = 0
    number = 0
    seen = False
    for char in raw:
        if char in _CN_DIGIT:
            number = _CN_DIGIT[char]
            seen = True
            continue
        if char == "十":
            section += (number or 1) * 10
        elif char == "百":
            section += (number or 1) * 100
        elif char == "千":
            section += (number or 1) * 1000
        elif char == "万":
            total += (section + number) * 10000
            section = 0
            number = 0
            seen = True
            continue
        else:
            return None
        number = 0
        seen = True
    if not seen:
        return None
    return total + section + number


def _parse_roman(raw: str) -> int | None:
    if not raw or any(char not in _ROMAN for char in raw):
        return None
    total = 0
    previous = 0
    for char in reversed(raw):
        value = _ROMAN[char]
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    return total


def _parse_number(lang: str, raw: str) -> int | None:
    if lang == "zh":
        return _parse_zh_number(raw)
    if raw.isdigit():
        return int(raw)
    return _parse_roman(raw)


def _role_for(lang: str, unit: str) -> str:
    if lang == "zh":
        return "volume" if unit == "卷" else "chapter"
    if unit.casefold() in {"quyển", "tập", "book", "volume"}:
        return "volume"
    return "chapter"


def _match_line(line: str) -> tuple[str, re.Match[str]] | None:
    if not line or len(line) > _SOFT_MAX_HEADING_LEN:
        return None
    if line.endswith(("。", "！", "？")):
        return None
    for lang, pattern in (("zh", _ZH_LINE), ("vi", _VI_LINE), ("en", _EN_LINE)):
        matched = pattern.match(line)
        if matched is None:
            continue
        if lang == "zh":
            if not _chinese_title_ok(line, matched.group("unit"), matched.group("after")):
                return None
        else:
            delim, title = _split_tail(matched.group("after"))
            if _tail_is_prose(title, lang=lang, had_delimiter=bool(delim)):
                return None
        return lang, matched
    return None


def is_chapter_heading(line: str) -> bool:
    """Return whether this single line is a plausible heading.

    This is the candidate test plus the pronoun tie-breaker. Numbering and
    layout are applied by :func:`split_chapters`, which may still reject a
    line this function accepts (a stray ``Chương 3`` inside chapter 57).
    """
    return _match_line(line.strip()) is not None


def _median(values: list[int]) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


def _layout_suspect(candidate: _Candidate, median: float) -> bool:
    """True for a wrap-width sentence sitting in the paragraph stream."""
    if median < 40 or candidate.blank_before or candidate.blank_after:
        return False
    if candidate.line_len < max(36, int(median * 0.9)):
        return False
    if candidate.lang == "zh":
        return len(candidate.title_text) >= 16
    if _is_strong_title(candidate.title_text):
        return False
    return len(candidate.title_text.split()) >= 8


def _promote_lone_volumes(candidates: list[_Candidate]) -> None:
    if candidates and all(item.role == "volume" for item in candidates):
        for item in candidates:
            item.role = "chapter"
            item.pattern_key = f"{item.lang}-chapter"


def _chapter_number_ok(number: int | None, last: int | None, volume_break: bool) -> bool:
    """Allow gaps and a repeated number. A smaller number needs a new volume.

    ``Chương 3`` inside chapter 57 goes backwards and is dropped. Two writings
    of the same number (``第一章`` then ``第1章``) are both kept.
    """
    if number is None or last is None:
        return True
    if number >= last:
        return True
    return volume_break


def _filter_candidates(
    candidates: list[_Candidate], body_lengths: list[int]
) -> tuple[list[_Candidate], float]:
    if not candidates:
        return [], 1.0
    _promote_lone_volumes(candidates)
    if len(candidates) == 1:
        return list(candidates), _single_confidence(candidates[0])

    median = _median(body_lengths) if len(body_lengths) >= 5 else 0
    pool = [item for item in candidates if not _layout_suspect(item, median)]
    if not pool:
        pool = list(candidates)

    chapter_pool = [item for item in pool if item.role == "chapter"]
    dominant_key = ""
    dominant_share = 1.0
    if chapter_pool:
        counts = Counter(item.pattern_key for item in chapter_pool)
        dominant_key, dominant_count = counts.most_common(1)[0]
        dominant_share = dominant_count / len(chapter_pool)

    kept: list[_Candidate] = []
    last_chapter: int | None = None
    last_volume: int | None = None
    volume_break = False
    for item in pool:
        if item.role == "volume":
            if item.number is not None and last_volume is not None and item.number <= last_volume:
                continue
            kept.append(item)
            if item.number is not None:
                last_volume = item.number
            volume_break = True
            continue
        if not _chapter_number_ok(item.number, last_chapter, volume_break):
            continue
        outlier = (
            bool(dominant_key)
            and item.pattern_key != dominant_key
            and dominant_share >= 0.75
            and len(chapter_pool) >= 4
            and not (last_chapter is None or item.number == last_chapter + 1)
        )
        if outlier:
            continue
        kept.append(item)
        if item.number is not None:
            last_chapter = item.number
        volume_break = False

    if not kept:
        kept = [pool[0]]
    confidence = _multi_confidence(
        raw_count=len(candidates),
        kept=kept,
        dominant_share=dominant_share,
    )
    return kept, confidence


def _single_confidence(candidate: _Candidate) -> float:
    words = candidate.title_text.split()
    if candidate.lang != "zh" and len(words) >= 6 and not _is_strong_title(candidate.title_text):
        return 0.66
    return 0.95


def _sequence_health(kept: list[_Candidate]) -> float:
    last: int | None = None
    volume_break = False
    checked = 0
    good = 0
    for item in kept:
        if item.role == "volume":
            volume_break = True
            continue
        if item.number is None:
            continue
        checked += 1
        if _chapter_number_ok(item.number, last, volume_break):
            good += 1
        last = item.number
        volume_break = False
    if checked == 0:
        return 1.0
    return good / checked


def _multi_confidence(*, raw_count: int, kept: list[_Candidate], dominant_share: float) -> float:
    if raw_count == 0:
        return 1.0
    drop_ratio = 1 - (len(kept) / raw_count)
    score = 0.97
    if drop_ratio >= 0.5:
        score -= 0.28
    elif drop_ratio >= 0.25:
        score -= 0.1
    elif drop_ratio > 0:
        score -= 0.03
    chapter_count = sum(1 for item in kept if item.role == "chapter")
    if chapter_count >= 4 and dominant_share < 0.6:
        score -= 0.28
    if _sequence_health(kept) < 0.8:
        score -= 0.18
    return max(0.35, min(0.99, score))


def _full_text(stripped: str, confidence: float) -> ChapterSplit:
    chapter = Chapter(
        index=1,
        title="Full text",
        body=stripped,
        char_count=len(stripped),
        kind=ChapterKind.CHAPTER,
        ordinal=1,
        chapter_number=None,
    )
    return ChapterSplit(
        chapters=[chapter],
        detection_confidence=confidence,
        uncertain=confidence < _UNCERTAIN_BELOW,
    )


def split_chapters_detailed(text: str) -> ChapterSplit:
    """Split ``text`` and score how reliable the heading decisions are."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines(keepends=True)
    contents = [line.rstrip("\r\n") for line in lines]
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line)

    raw: list[_Candidate] = []
    for index, content in enumerate(contents):
        stripped = content.strip()
        matched = _match_line(stripped)
        if matched is None:
            continue
        lang, found = matched
        title_text = _split_tail(found.group("after"))[1]
        unit = found.group("unit")
        role = _role_for(lang, unit)
        raw.append(
            _Candidate(
                start=offsets[index],
                end=offsets[index] + len(content),
                title=stripped,
                lang=lang,
                role=role,
                number=_parse_number(lang, found.group("num")),
                title_text=title_text,
                pattern_key=f"{lang}-{role}",
                blank_before=index > 0 and contents[index - 1].strip() == "",
                blank_after=index + 1 < len(contents) and contents[index + 1].strip() == "",
                line_len=len(stripped),
            )
        )

    body_lengths = [
        len(content.strip())
        for content in contents
        if content.strip() and not any(content.strip() == item.title for item in raw)
    ]
    kept, confidence = _filter_candidates(raw, body_lengths)
    confidence = round(confidence, 2)
    if not kept:
        return _full_text(normalized.strip(), confidence)

    chapters: list[Chapter] = []
    ordinal = 1
    preface = normalized[: kept[0].start].strip()
    if preface:
        chapters.append(
            Chapter(
                index=ordinal,
                title="",
                body=preface,
                char_count=len(preface),
                kind=ChapterKind.PREFACE,
                ordinal=ordinal,
                chapter_number=None,
            )
        )
        ordinal += 1

    for index, heading in enumerate(kept):
        body_end = kept[index + 1].start if index + 1 < len(kept) else len(normalized)
        body = normalized[heading.end : body_end].strip()
        kind = ChapterKind.VOLUME if heading.role == "volume" else ChapterKind.CHAPTER
        number = heading.number if kind is ChapterKind.CHAPTER else None
        chapters.append(
            Chapter(
                index=ordinal,
                title=heading.title,
                body=body,
                char_count=len(body),
                kind=kind,
                ordinal=ordinal,
                chapter_number=number,
            )
        )
        ordinal += 1

    return ChapterSplit(
        chapters=chapters,
        detection_confidence=confidence,
        uncertain=confidence < _UNCERTAIN_BELOW,
    )


def split_chapters(text: str) -> list[Chapter]:
    return split_chapters_detailed(text).chapters


def chunk_by_chapter(chapters: list[Chapter], max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    chunks: list[Chunk] = []
    global_index = 1
    ordinal = 0
    for chapter in chapters:
        body = chapter.body.strip()
        # A heading with no body (for example a volume title) belongs to the next chunk.
        chunk_chapter = ordinal + 1
        if body:
            ordinal += 1
        title = chapter.title.strip()
        if not body:
            if title and title != "Full text":
                text_to_chunk = title
            else:
                continue
        elif title and title != "Full text":
            text_to_chunk = f"{title}\n\n{body}"
        else:
            text_to_chunk = body

        chapter_chunks = chunk_source(text_to_chunk, max_chars=max_chars)
        for piece in chapter_chunks:
            chunks.append(
                Chunk(
                    id=f"chunk-{global_index:03d}",
                    text=piece.text,
                    index=global_index,
                    chapter=chunk_chapter,
                )
            )
            global_index += 1
    return chunks
