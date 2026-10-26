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


def merge_srt_cues(
    cues: list[SRTCue],
    max_pause_ms: int = 400,
    max_chunk_duration_ms: int = 20000,
) -> list[TimedChunk]:
    if max_pause_ms < 0:
        raise ValueError("max_pause_ms must be >= 0")
    if max_chunk_duration_ms <= 0:
        raise ValueError("max_chunk_duration_ms must be > 0")

    valid_cues = [c for c in cues if c.text and c.text.strip()]
    if not valid_cues:
        return []

    sorted_cues = sorted(valid_cues, key=lambda c: (c.start_ms, c.index))

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

        prev_ended_sentence = is_sentence_terminal(prev.text)
        pause_exceeded = pause_ms > max_pause_ms
        duration_exceeded = candidate_duration_ms > max_chunk_duration_ms

        if prev_ended_sentence or pause_exceeded or duration_exceeded:
            flush_chunk()

        current_cues.append(cue)

    flush_chunk()
    return chunks
