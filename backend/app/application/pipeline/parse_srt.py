import html
import re
from collections.abc import Sequence

from app.domain.srt import SRTCue

_TIMECODE_LINE_RE = re.compile(
    r"^\s*(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{1,3})"
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_BRACKET_CUE_RE = re.compile(r"^\s*(?:\[[^\]]+\]|[♪♫][^♪♫]*[♪♫])\s*$")
_ASIDE_RE = re.compile(r"^\s*(?:\((.+)\)|（(.+)）|\*+(.+?)\*+)\s*$")
_SPEECH_PUNCTUATION = frozenset(".?!…。？！")
# Caseless scripts have no word spaces; only very short asides there are sound cues.
_CASELESS_CUE_MAX_CHARS = 4
_CASED_CUE_MAX_WORDS = 3
INLINE_SOUND_EFFECT_RE = re.compile(
    r"\[(?:Music|Applause|Laughter|Sighs|Gasps|Cheering|Groans|Crying)\]"
    r"|\((?:applause|cheering|laughter|sighs|music)\)|[♪♫]",
    re.IGNORECASE,
)


def is_sound_effect_line(line: str) -> bool:
    """Whole-line sound cue, e.g. ``[Music]``, ``♪ ... ♪``, ``(sighs)``, ``*APPLAUSE*``.

    ``[..]`` and ``♪..♪`` are always cues. ``(..)``/``（..）``/``*..*`` are also used for inner
    thoughts, notes and emphasis, so they count only when they read like a cue: no sentence
    punctuation, all-lowercase or all-uppercase (or a single word) in cased scripts, at most
    ``_CASED_CUE_MAX_WORDS`` words, and at most ``_CASELESS_CUE_MAX_CHARS`` chars in caseless ones.
    """
    if _BRACKET_CUE_RE.match(line):
        return True
    match = _ASIDE_RE.match(line)
    if match is None:
        return False
    inner = next(group for group in match.groups() if group is not None).strip()
    if not inner or any(ch in _SPEECH_PUNCTUATION for ch in inner):
        return False
    cased = [ch for ch in inner if ch.isupper() or ch.islower()]
    if not cased:
        return len(inner.replace(" ", "")) <= _CASELESS_CUE_MAX_CHARS
    words = inner.split()
    single_case = all(ch.islower() for ch in cased) or all(ch.isupper() for ch in cased)
    # Sentence case ("He thinks so") reads as speech; one title-case word ("Sighs") is a cue.
    return len(words) <= _CASED_CUE_MAX_WORDS and (single_case or len(words) == 1)


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
        had_text = False
        for line in lines[tc_index + 1 :]:
            cleaned = _HTML_TAG_RE.sub("", line)
            cleaned = _ASS_TAG_RE.sub("", cleaned).strip()
            cleaned = html.unescape(cleaned).strip()
            if not cleaned:
                continue
            had_text = True
            if is_sound_effect_line(cleaned):
                continue
            cleaned = " ".join(INLINE_SOUND_EFFECT_RE.sub("", cleaned).split())
            if cleaned:
                text_lines.append(cleaned)

        cue_text = " ".join(text_lines)
        if had_text and not cue_text:
            # Sound-effect-only cue: nothing to voice, its time becomes silence.
            continue
        previous = cues[-1] if cues else None
        merged = None
        close = previous is not None and start_ms - previous.end_ms <= ROLLING_CUE_MAX_GAP_MS
        if previous is not None and close and previous.text and cue_text:
            merged = merge_rolling_cue(previous.text, cue_text)
        if previous is not None and merged is not None:
            # Rolling captions repeat earlier words; keep one cue spanning both.
            cues[-1] = SRTCue(
                index=previous.index,
                start_ms=previous.start_ms,
                end_ms=max(previous.end_ms, end_ms),
                text=merged,
            )
            continue
        cues.append(
            SRTCue(
                index=cue_index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=cue_text,
            )
        )

    return cues


_MIN_ROLLING_OVERLAP_WORDS = 2
# Timed cues further apart than this are separate utterances, even when identical.
ROLLING_CUE_MAX_GAP_MS = 1000


def merge_rolling_cue(previous: str, current: str) -> str | None:
    """Collapse progressive/rolling captions into one block.

    Identical consecutive cues are dropped (so a repeated "No." / "No." is narrated once),
    a cue that extends the previous one replaces it, and a cue whose first words repeat
    at least ``_MIN_ROLLING_OVERLAP_WORDS`` trailing words of the previous one is appended
    without the overlap. Returns ``None`` when the cues are unrelated.
    """
    prev_words = previous.split()
    cur_words = current.split()
    extends = cur_words[: len(prev_words)] == prev_words
    # A one-word cue ("No.") followed by "No way." is two utterances, not a rolling caption.
    if extends and (cur_words == prev_words or len(prev_words) >= _MIN_ROLLING_OVERLAP_WORDS):
        return current
    if (
        _MIN_ROLLING_OVERLAP_WORDS <= len(cur_words) <= len(prev_words)
        and prev_words[-len(cur_words) :] == cur_words
    ):
        return previous
    for overlap in range(min(len(prev_words), len(cur_words)) - 1, 0, -1):
        if overlap < _MIN_ROLLING_OVERLAP_WORDS:
            break
        if prev_words[-overlap:] == cur_words[:overlap]:
            return " ".join(prev_words + cur_words[overlap:])
    return None


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
