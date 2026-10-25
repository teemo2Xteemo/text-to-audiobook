import re

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import resolve_source_language
from app.domain.ports import LanguageDetector

_SRT_TIMECODE_RE = re.compile(
    r"^\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}.*$",
    re.MULTILINE,
)
_SRT_INDEX_RE = re.compile(r"^\s*\d+\s*$")
_SRT_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def strip_srt(text: str) -> str:
    if not _SRT_TIMECODE_RE.search(text):
        return text
    blocks = re.split(r"\n{2,}", text)
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
