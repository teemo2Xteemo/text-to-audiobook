from app.domain.chunking import CHUNK_MAX_CHARS, _atomic_units
from app.domain.srt import SRTCue, TimedChunk

SENTENCE_TERMINALS = frozenset(".?!。！？．｡…")
CLOSING_PUNCTUATION = frozenset("\"'”’»）」』】")
COMMON_ABBREVIATIONS = frozenset(
    {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.", "e.g.", "i.e."}
)


def is_sentence_terminal(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False

    while cleaned and cleaned[-1] in CLOSING_PUNCTUATION:
        cleaned = cleaned[:-1].strip()

    if not cleaned:
        return False

    if cleaned[-1] not in SENTENCE_TERMINALS:
        return False

    words = cleaned.split()
    if words and words[-1].lower() in COMMON_ABBREVIATIONS:
        return False

    return True


def _split_oversized_cue(cue: SRTCue, max_chars: int) -> list[SRTCue]:
    if len(cue.text) <= max_chars:
        return [cue]

    units = _atomic_units(cue.text, max_chars)
    parts: list[str] = []
    current = ""
    for u in units:
        candidate = (current + " " + u).strip() if current else u
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                parts.append(current)
            current = u
    if current:
        parts.append(current)

    if not parts:
        return [cue]

    total_chars = max(1, sum(len(p) for p in parts))
    total_duration = max(0, cue.end_ms - cue.start_ms)
    sub_cues: list[SRTCue] = []
    cur_start = cue.start_ms

    for idx, part in enumerate(parts):
        if idx == len(parts) - 1:
            cur_end = cue.end_ms
        else:
            part_dur = int(round(total_duration * (len(part) / total_chars)))
            cur_end = min(cue.end_ms, cur_start + part_dur)
        sub_cues.append(
            SRTCue(
                index=cue.index,
                start_ms=cur_start,
                end_ms=max(cur_start, cur_end),
                text=part,
            )
        )
        cur_start = cur_end

    return sub_cues


def merge_srt_cues(
    cues: list[SRTCue],
    max_pause_ms: int = 400,
    max_chunk_duration_ms: int = 20000,
    max_chars: int = CHUNK_MAX_CHARS,
) -> list[TimedChunk]:
    if max_pause_ms < 0:
        raise ValueError("max_pause_ms must be >= 0")
    if max_chunk_duration_ms <= 0:
        raise ValueError("max_chunk_duration_ms must be > 0")
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")

    valid_cues = [c for c in cues if c.text and c.text.strip()]
    if not valid_cues:
        return []

    expanded_cues: list[SRTCue] = []
    for c in valid_cues:
        expanded_cues.extend(_split_oversized_cue(c, max_chars))

    sorted_cues = sorted(expanded_cues, key=lambda c: (c.start_ms, c.index))

    chunks: list[TimedChunk] = []
    current_cues: list[SRTCue] = []

    def flush_chunk() -> None:
        if not current_cues:
            return
        chunk_text = " ".join(c.text.strip() for c in current_cues)
        chunks.append(
            TimedChunk(
                chunk_index=len(chunks),
                start_ms=current_cues[0].start_ms,
                end_ms=max(c.end_ms for c in current_cues),
                text=chunk_text,
                cue_indices=[c.index for c in current_cues],
            )
        )
        current_cues.clear()

    for cue in sorted_cues:
        if not current_cues:
            current_cues.append(cue)
            continue

        prev = current_cues[-1]
        pause_ms = cue.start_ms - prev.end_ms
        candidate_end_ms = max(max(c.end_ms for c in current_cues), cue.end_ms)
        candidate_duration_ms = candidate_end_ms - current_cues[0].start_ms
        candidate_text_len = (
            sum(len(c.text.strip()) for c in current_cues)
            + len(cue.text.strip())
            + len(current_cues)
        )

        prev_ended_sentence = is_sentence_terminal(prev.text)
        pause_exceeded = pause_ms > max_pause_ms
        duration_exceeded = candidate_duration_ms > max_chunk_duration_ms
        chars_exceeded = candidate_text_len > max_chars

        if prev_ended_sentence or pause_exceeded or duration_exceeded or chars_exceeded:
            flush_chunk()

        current_cues.append(cue)

    flush_chunk()
    return chunks
