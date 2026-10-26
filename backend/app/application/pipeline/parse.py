import re

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import resolve_source_language
from app.domain.ports import LanguageDetector

__all__ = ["is_srt_document", "parse_source", "strip_srt"]

_SRT_TIMECODE_RE = re.compile(
    r"^\s*(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*(?:\d+:)?\d{2}:\d{2}[,.]\d{1,3}.*$",
    re.MULTILINE,
)
_SRT_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_SRT_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


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
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n{2,}", normalized)
    if not _is_srt_document(blocks):
        return text
    cleaned_blocks: list[str] = []
    for block in blocks:
        lines: list[str] = []
        for raw_line in block.split("\n"):
            line = raw_line.strip()
            if not line or line.upper() == "WEBVTT":
                continue
            if _SRT_INDEX_RE.match(line) or _SRT_TIMECODE_RE.match(line):
                continue
            cleaned = _SRT_HTML_TAG_RE.sub("", line).strip()
            if cleaned:
                lines.append(cleaned)
        if lines:
            cleaned_blocks.append(" ".join(lines))
    return "\n\n".join(cleaned_blocks).strip()


async def parse_source(
    text: str, source_language: str, detector: LanguageDetector
) -> tuple[str, str]:
    normalized = strip_srt(text.replace("\r\n", "\n").replace("\r", "\n").strip())
    if not normalized:
        raise DomainError(ErrorType.INVALID_INPUT, "text is required")
    resolved = await resolve_source_language(normalized, source_language, detector)
    return normalized, resolved
