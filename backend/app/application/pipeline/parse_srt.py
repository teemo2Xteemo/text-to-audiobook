import html
import re
from collections.abc import Sequence

from app.domain.srt import SRTCue

_TIMECODE_LINE_RE = re.compile(
    r"^\s*(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def _timecode_to_ms(hours: str | None, minutes: str, seconds: str, millis: str) -> int:
    h = int(hours) if hours is not None else 0
    m = int(minutes)
    s = int(seconds)
    ms = int(millis.ljust(3, "0")[:3])
    return (h * 3600 + m * 60 + s) * 1000 + ms


def parse_timecode_line(line: str) -> tuple[int, int] | None:
    """Return ``(start_ms, end_ms)`` for an SRT/VTT timecode line, else ``None``."""
    match = _TIMECODE_LINE_RE.match(line)
    if match is None:
        return None
    start = _timecode_to_ms(match.group(1), match.group(2), match.group(3), match.group(4))
    end = _timecode_to_ms(match.group(5), match.group(6), match.group(7), match.group(8))
    return start, end


def format_srt_timecode(ms: int) -> str:
    total_ms = max(0, ms)
    hours = total_ms // 3600000
    rem = total_ms % 3600000
    minutes = rem // 60000
    rem %= 60000
    seconds = rem // 1000
    millis = rem % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def parse_srt_cues(text: str) -> list[SRTCue]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()
    if not normalized:
        return []

    blocks = re.split(r"\n{2,}", normalized)
    cues: list[SRTCue] = []

    for block in blocks:
        lines = [line.strip() for line in block.split("\n")]
        tc_index: int | None = None
        tc_match: re.Match[str] | None = None

        for idx, line in enumerate(lines):
            match = _TIMECODE_LINE_RE.match(line)
            if match:
                tc_index = idx
                tc_match = match
                break

        if tc_index is None or tc_match is None:
            continue

        cue_index = len(cues) + 1
        if tc_index > 0:
            candidate_index = lines[tc_index - 1]
            if candidate_index.isdigit():
                cue_index = int(candidate_index)

        start_ms = _timecode_to_ms(
            tc_match.group(1), tc_match.group(2), tc_match.group(3), tc_match.group(4)
        )
        end_ms = _timecode_to_ms(
            tc_match.group(5), tc_match.group(6), tc_match.group(7), tc_match.group(8)
        )

        text_lines: list[str] = []
        for line in lines[tc_index + 1 :]:
            cleaned = _HTML_TAG_RE.sub("", line)
            cleaned = _ASS_TAG_RE.sub("", cleaned).strip()
            cleaned = html.unescape(cleaned).strip()
            if cleaned:
                text_lines.append(cleaned)

        cue_text = " ".join(text_lines)
        cues.append(
            SRTCue(
                index=cue_index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=cue_text,
            )
        )

    return cues


def serialize_srt_cues(cues: Sequence[SRTCue]) -> str:
    blocks: list[str] = []
    for idx, cue in enumerate(cues, start=1):
        start_tc = format_srt_timecode(cue.start_ms)
        end_tc = format_srt_timecode(cue.end_ms)
        raw_text = cue.text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
        clean_text = "\n".join(lines)
        blocks.append(f"{idx}\n{start_tc} --> {end_tc}\n{clean_text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")
