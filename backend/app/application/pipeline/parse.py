import re

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import resolve_source_language
from app.domain.ports import LanguageDetector

__all__ = ["is_srt_document", "parse_source", "prepare_narration_text", "strip_srt"]

_SRT_TIMECODE_RE = re.compile(
    r"^\s*(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3}.*$",
    re.MULTILINE,
)
_SRT_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_SRT_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_SOUND_EFFECT_RE = re.compile(r"^\s*(?:\[[^\]]+\]|\([^)]+\)|[♪♫][^♪♫]*[♪♫]|\*+[^*]+\*+)\s*$")
_INLINE_SOUND_EFFECT_RE = re.compile(
    r"\[(?:Music|Applause|Laughter|Sighs|Gasps|Cheering|Groans|Crying)\]|\((?:applause|cheering|laughter|sighs|music)\)|[♪♫]",
    re.IGNORECASE,
)
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


def strip_srt(text: str) -> str:
    normalized = _INVISIBLE_CHARS_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n").strip())
    blocks = re.split(r"\n{2,}", normalized)
    if not _is_srt_document(blocks):
        return text
    cleaned_blocks: list[str] = []
    for block in blocks:
        lines: list[str] = []
        for raw_line in block.split("\n"):
            line = _INVISIBLE_CHARS_RE.sub("", raw_line).strip()
            if not line or line.upper() == "WEBVTT":
                continue
            if _SRT_INDEX_RE.match(line) or _SRT_TIMECODE_RE.match(line):
                continue
            if _SOUND_EFFECT_RE.match(line):
                continue
            cleaned = _SRT_HTML_TAG_RE.sub("", line)
            cleaned = _ASS_TAG_RE.sub("", cleaned)
            cleaned = _INLINE_SOUND_EFFECT_RE.sub("", cleaned)
            cleaned = re.sub(r"^\s*[-–—]\s*", "", cleaned)
            cleaned = re.sub(r"\s+[-–—]\s*", " ", cleaned)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                lines.append(cleaned)
        if lines:
            block_text = " ".join(lines)
            if not cleaned_blocks or cleaned_blocks[-1] != block_text:
                cleaned_blocks.append(block_text)
    return "\n\n".join(cleaned_blocks).strip()


async def parse_source(
    text: str, source_language: str, detector: LanguageDetector
) -> tuple[str, str]:
    normalized = strip_srt(text.replace("\r\n", "\n").replace("\r", "\n").strip())
    if not normalized:
        raise DomainError(ErrorType.INVALID_INPUT, "text is required")
    resolved = await resolve_source_language(normalized, source_language, detector)
    return normalized, resolved
