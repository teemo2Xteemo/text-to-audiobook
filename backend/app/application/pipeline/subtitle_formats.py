"""Lossless SRT, WebVTT and ASS parse/render for translate-only jobs.

The narration parser drops sound-effect cues, strips tags and merges rolling
captions. This module keeps cue count, order, indices and timecodes, and
changes only the text that is translated.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence

from app.application.pipeline.parse import is_srt_document
from app.application.pipeline.parse_srt import (
    format_srt_timecode,
    is_sound_effect_line,
    parse_timecode_line,
)
from app.domain.subtitles import SUBTITLE_FORMATS, CueSlot, DocumentFormat, SubtitleDocument

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_LEADING_OVERRIDE_RE = re.compile(r"^(?:\{[^}]*\})+")
_ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{1,2})$")

_ASS_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding"
)
_ASS_STYLE = (
    "Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
    "0,0,0,0,100,100,0,0,1,3,0,2,60,60,50,1"
)
_ASS_HEADER = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n"
    "PlayResX: 1920\n"
    "PlayResY: 1080\n"
    "\n"
    "[V4+ Styles]\n"
    f"{_ASS_STYLE_FORMAT}\n"
    f"{_ASS_STYLE}\n"
    "\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def normalize_subtitle_text(text: str) -> str:
    """Drop a leading BOM and normalise newlines to ``\\n``."""
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def detect_document_format(text: str) -> DocumentFormat:
    """Detect SRT, WebVTT or ASS by content. Anything else is plain text."""
    normalized = normalize_subtitle_text(text)
    if _is_ass_document(normalized):
        return DocumentFormat.ASS
    if _is_vtt_document(normalized):
        return DocumentFormat.VTT
    if is_srt_document(normalized):
        return DocumentFormat.SRT
    return DocumentFormat.TXT


def parse_document(text: str) -> SubtitleDocument | None:
    """Parse a subtitle document. Plain text returns ``None``."""
    normalized = normalize_subtitle_text(text)
    fmt = detect_document_format(normalized)
    if fmt not in SUBTITLE_FORMATS:
        return None
    if fmt is DocumentFormat.ASS:
        return _parse_ass(normalized)
    return _parse_timed(normalized, fmt)


def render_document(doc: SubtitleDocument, texts: Sequence[str]) -> str:
    """Replace each translatable cue. ``len(texts)`` must match the cue count.

    Passing a cue's own ``raw`` reproduces that cue. A different string is the
    translation: a leading override tag is reattached and the cue stays one line.
    """
    cues = doc.cues
    if len(texts) != len(cues):
        raise ValueError(f"expected {len(cues)} cue texts, got {len(texts)}")
    rendered: list[str] = []
    cue_index = 0
    for part in doc.parts:
        if isinstance(part, str):
            rendered.append(part)
            continue
        rendered.append(_render_cue(part, texts[cue_index]))
        cue_index += 1
    return "".join(rendered)


def write_timed_document(fmt: DocumentFormat, cues: Sequence[tuple[int, int, str]]) -> str:
    """Build a new timed subtitle from ``(start_ms, end_ms, text)`` cues."""
    if fmt is DocumentFormat.TXT:
        raise ValueError("txt output has no cue timing")
    if fmt is DocumentFormat.SRT:
        blocks = [
            f"{index}\n{format_srt_timecode(start)} --> {format_srt_timecode(end)}\n{text}\n"
            for index, (start, end, text) in enumerate(cues, start=1)
        ]
        return "\n".join(blocks)
    if fmt is DocumentFormat.VTT:
        blocks = [
            f"{_format_vtt_timecode(start)} --> {_format_vtt_timecode(end)}\n{text}\n"
            for start, end, text in cues
        ]
        return "WEBVTT\n\n" + "\n".join(blocks)
    if fmt is DocumentFormat.ASS:
        lines = [
            (
                "Dialogue: 0,"
                f"{_format_ass_timecode(start)},{_format_ass_timecode(end)},Default,,0,0,0,,{text}"
            )
            for start, end, text in cues
        ]
        body = "\n".join(lines)
        if body:
            body += "\n"
        return _ASS_HEADER + body
    raise ValueError(f"unsupported document format: {fmt}")


def _is_ass_document(text: str) -> bool:
    has_section = False
    has_dialogue = False
    for line in text.split("\n"):
        stripped = line.strip().lower()
        if stripped in {"[script info]", "[events]"}:
            has_section = True
        if stripped.startswith("dialogue:"):
            has_dialogue = True
        if has_section and has_dialogue:
            return True
    return False


def _is_vtt_document(text: str) -> bool:
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.upper().startswith("WEBVTT")
    return False


def _parse_timed(text: str, fmt: DocumentFormat) -> SubtitleDocument:
    parts: list[str | CueSlot] = []
    blocks = re.split(r"(\n{2,})", text)
    index = 0
    while index < len(blocks):
        content = blocks[index]
        separator = blocks[index + 1] if index + 1 < len(blocks) else ""
        index += 2
        if content == "":
            if separator:
                parts.append(separator)
            continue
        if _keep_block_raw(content, vtt=fmt is DocumentFormat.VTT):
            parts.append(content)
        else:
            parsed = _parse_cue_block(content)
            if parsed is None:
                parts.append(content)
            else:
                prefix, slot = parsed
                if prefix:
                    parts.append(prefix)
                parts.append(slot)
        if separator:
            parts.append(separator)
    return SubtitleDocument(format=fmt, parts=tuple(parts))


def _keep_block_raw(content: str, *, vtt: bool) -> bool:
    if not vtt:
        return False
    first = content.lstrip("\ufeff").lstrip("\n").split("\n", 1)[0].strip().upper()
    if first.startswith(("NOTE", "STYLE", "REGION")):
        return True
    if first.startswith("WEBVTT") and "-->" not in content:
        return True
    return False


def _parse_cue_block(block: str) -> tuple[str, CueSlot] | None:
    lines = block.split("\n")
    tc_index: int | None = None
    timing: tuple[int, int] | None = None
    for index, line in enumerate(lines):
        if "-->" not in line:
            continue
        parsed = parse_timecode_line(line)
        if parsed is None:
            continue
        tc_index = index
        timing = parsed
        break
    if tc_index is None or timing is None:
        return None
    cursor = 0
    for index, line in enumerate(lines):
        if index == tc_index:
            end = cursor + len(line)
            if end < len(block) and block[end] == "\n":
                end += 1
            return block[:end], _make_slot(timing[0], timing[1], block[end:])
        cursor += len(line) + 1
    return None


def _parse_ass(text: str) -> SubtitleDocument:
    parts: list[str | CueSlot] = []
    pending: list[str] = []
    in_events = False
    field_names = [
        "layer",
        "start",
        "end",
        "style",
        "name",
        "marginl",
        "marginr",
        "marginv",
        "effect",
        "text",
    ]
    text_field = 9

    def flush() -> None:
        if pending:
            parts.append("".join(pending))
            pending.clear()

    logical = text.splitlines(keepends=True)
    for line in logical:
        stripped = line.rstrip("\n")
        section = stripped.strip().lower()
        if section.startswith("[") and section.endswith("]"):
            in_events = section == "[events]"
            pending.append(line)
            continue
        if in_events and section.startswith("format:"):
            names = [item.strip().lower() for item in stripped.split(":", 1)[1].split(",")]
            if names:
                field_names = names
                text_field = names.index("text") if "text" in names else len(names) - 1
            pending.append(line)
            continue
        if in_events and section.startswith("dialogue:"):
            parsed = _split_ass_dialogue(stripped, field_names, text_field)
            if parsed is None:
                pending.append(line)
                continue
            raw_prefix, slot = parsed
            newline = line[len(stripped) :]
            flush()
            parts.append(raw_prefix)
            parts.append(slot)
            if newline:
                pending.append(newline)
            continue
        pending.append(line)
    flush()
    return SubtitleDocument(format=DocumentFormat.ASS, parts=tuple(parts))


def _split_ass_dialogue(
    stripped: str, field_names: list[str], text_field: int
) -> tuple[str, CueSlot] | None:
    label, rest = stripped.split(":", 1)
    field_count = max(len(field_names), text_field + 1)
    fields = rest.split(",", field_count - 1)
    if len(fields) <= text_field:
        return None
    text_value = fields[text_field]
    raw_prefix = f"{label}:{','.join(fields[:text_field])},"
    start_ms = _ass_time_ms(_field(fields, field_names, "start"))
    end_ms = _ass_time_ms(_field(fields, field_names, "end"))
    return raw_prefix, _make_slot(start_ms, end_ms, text_value)


def _field(fields: list[str], names: list[str], name: str) -> str:
    if name not in names:
        return ""
    index = names.index(name)
    if index >= len(fields):
        return ""
    return fields[index]


def _ass_time_ms(value: str) -> int:
    match = _ASS_TIME_RE.match(value.strip())
    if match is None:
        return 0
    hours, minutes, seconds, centis = (int(group) for group in match.groups())
    centis = centis * 10 if centis < 10 and len(match.group(4)) == 1 else centis
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + centis * 10


def _make_slot(start_ms: int, end_ms: int, raw: str) -> CueSlot:
    prefix_match = _LEADING_OVERRIDE_RE.match(raw)
    prefix = prefix_match.group(0) if prefix_match else ""
    cleaned = raw.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    cleaned = cleaned.replace("\n", " ")
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = _ASS_TAG_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    text = " ".join(cleaned.split())
    translatable = bool(text) and not is_sound_effect_line(text)
    return CueSlot(
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
        raw=raw,
        prefix=prefix,
        translatable=translatable,
    )


def _render_cue(slot: CueSlot, text: str) -> str:
    if not slot.translatable or text == slot.raw:
        return slot.raw
    body = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    if slot.prefix and not body.startswith(slot.prefix):
        body = f"{slot.prefix}{body}"
    if slot.raw.endswith("\n"):
        body += "\n"
    return body


def _format_vtt_timecode(ms: int) -> str:
    return format_srt_timecode(ms).replace(",", ".")


def _format_ass_timecode(ms: int) -> str:
    total = max(0, ms)
    hours = total // 3_600_000
    remainder = total % 3_600_000
    minutes = remainder // 60_000
    remainder %= 60_000
    seconds = remainder // 1000
    centis = (remainder % 1000) // 10
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"
