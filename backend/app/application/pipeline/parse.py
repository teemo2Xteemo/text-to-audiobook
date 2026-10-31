import re

from app.application.pipeline.merge_srt import is_sentence_terminal
from app.application.pipeline.parse_srt import (
    INLINE_SOUND_EFFECT_RE,
    is_sound_effect_line,
    merge_rolling_cue,
    parse_timecode_line,
)
from app.domain.errors import DomainError, ErrorType
from app.domain.languages import resolve_source_language
from app.domain.ports import LanguageDetector

__all__ = [
    "SRT_PARAGRAPH_GAP_MS",
    "is_srt_document",
    "parse_source",
    "prepare_narration_text",
    "strip_srt",
]

# Silence between cues that starts a new narration paragraph (spec #28 §7.4).
SRT_PARAGRAPH_GAP_MS = 2000

_SRT_TIMECODE_RE = re.compile(
    r"^\s*(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3}.*$",
    re.MULTILINE,
)
_SRT_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_SRT_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_DIVIDER_LINE_RE = re.compile(r"(?m)^\s*([*=-]\s*){3,}\s*$")
_CONSECUTIVE_NEWLINES_RE = re.compile(r"\n{3,}")
_INVISIBLE_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad\u200e\u200f]")


def prepare_narration_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _INVISIBLE_CHARS_RE.sub("", normalized)
    normalized = normalized.replace("\u3000", "  ")
    normalized = _DIVIDER_LINE_RE.sub("", normalized)
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = _CONSECUTIVE_NEWLINES_RE.sub("\n\n", normalized)
    return normalized.strip()


def _is_srt_cue_block(block: str) -> bool:
    lines = [line.strip() for line in block.split("\n") if line.strip()]
    if not lines:
        return False
    if _SRT_TIMECODE_RE.match(lines[0]):
        return True
    return len(lines) >= 2 and bool(
        _SRT_INDEX_RE.match(lines[0]) and _SRT_TIMECODE_RE.match(lines[1])
    )


def _is_srt_document(blocks: list[str]) -> bool:
    non_empty = [block.strip() for block in blocks if block.strip()]
    if not non_empty:
        return False
    first = non_empty[0].lstrip("\ufeff")
    first_lines = [line.strip() for line in first.split("\n") if line.strip()]
    if first_lines and first_lines[0].upper().startswith("WEBVTT"):
        remaining = "\n".join(first_lines[1:])
        if remaining and _is_srt_cue_block(remaining):
            return True
        return len(non_empty) >= 2 and _is_srt_cue_block(non_empty[1])
    return _is_srt_cue_block(first)


def is_srt_document(text_or_blocks: str | list[str]) -> bool:
    if isinstance(text_or_blocks, str):
        normalized = text_or_blocks.replace("\r\n", "\n").replace("\r", "\n").strip()
        blocks = re.split(r"\n{2,}", normalized)
    else:
        blocks = text_or_blocks
    return _is_srt_document(blocks)


def strip_srt(text: str, *, paragraph_gap_ms: int = SRT_PARAGRAPH_GAP_MS) -> str:
    """Turn subtitles into continuous narration text.

    Cues are joined into one paragraph. A new paragraph starts when the silence before a
    cue exceeds ``paragraph_gap_ms`` and the previous cue ends a sentence, or exceeds twice
    that regardless of punctuation, so TTS does not pause mid-sentence at short cue gaps.
    """
    normalized = _INVISIBLE_CHARS_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n").strip())
    blocks = re.split(r"\n{2,}", normalized)
    if not _is_srt_document(blocks):
        return text
    paragraphs: list[list[str]] = []
    previous_end_ms: int | None = None
    for block in blocks:
        lines: list[str] = []
        timing: tuple[int, int] | None = None
        for raw_line in block.split("\n"):
            line = _INVISIBLE_CHARS_RE.sub("", raw_line).strip()
            if not line or line.upper() == "WEBVTT":
                continue
            if _SRT_TIMECODE_RE.match(line):
                timing = parse_timecode_line(line)
                continue
            if _SRT_INDEX_RE.match(line):
                continue
            if is_sound_effect_line(line):
                continue
            cleaned = _SRT_HTML_TAG_RE.sub("", line)
            cleaned = _ASS_TAG_RE.sub("", cleaned)
            cleaned = INLINE_SOUND_EFFECT_RE.sub("", cleaned)
            cleaned = re.sub(r"^\s*[-–—]\s*", "", cleaned)
            cleaned = re.sub(r"\s+[-–—]\s*", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                lines.append(cleaned)
        if not lines:
            continue
        cue = " ".join(lines)
        merged = merge_rolling_cue(paragraphs[-1][-1], cue) if paragraphs else None
        if merged is not None:
            paragraphs[-1][-1] = merged
        elif paragraphs and not _starts_paragraph(
            paragraphs[-1][-1], previous_end_ms, timing, paragraph_gap_ms
        ):
            paragraphs[-1].append(cue)
        else:
            paragraphs.append([cue])
        if timing is not None:
            previous_end_ms = timing[1]
    return "\n\n".join(" ".join(cues) for cues in paragraphs).strip()


def _starts_paragraph(
    previous_cue: str,
    previous_end_ms: int | None,
    timing: tuple[int, int] | None,
    paragraph_gap_ms: int,
) -> bool:
    if previous_end_ms is None or timing is None:
        return False
    gap = timing[0] - previous_end_ms
    # Unpunctuated (auto-generated) captions still get breaks at clearly long silences.
    if gap > 2 * paragraph_gap_ms:
        return True
    return gap > paragraph_gap_ms and is_sentence_terminal(previous_cue)


async def parse_source(
    text: str, source_language: str, detector: LanguageDetector
) -> tuple[str, str]:
    normalized = strip_srt(text.replace("\r\n", "\n").replace("\r", "\n").strip())
    if not normalized:
        raise DomainError(ErrorType.INVALID_INPUT, "text is required")
    resolved = await resolve_source_language(normalized, source_language, detector)
    return normalized, resolved
