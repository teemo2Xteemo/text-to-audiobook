"""Group subtitle cues into sentences and split a translation back onto them."""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.application.pipeline.merge_srt import is_sentence_terminal
from app.application.pipeline.parse import SRT_PARAGRAPH_GAP_MS
from app.domain.subtitles import CueSlot

MAX_CUES_PER_SENTENCE = 5
_SNAP_PUNCTUATION = frozenset(".,;:!?…。！？，、；：．｡")
_SENTENCE_PUNCTUATION = frozenset(".?!。！？…．｡")
_COMMA_PUNCTUATION = frozenset(",，、")


def group_sentences(cues: Sequence[CueSlot]) -> list[list[int]]:
    """Indices of consecutive translatable cues that form one translation unit.

    A group closes on sentence-ending punctuation, before a pause longer than
    a narration paragraph gap, at ``MAX_CUES_PER_SENTENCE``, or when a
    non-translatable cue (a sound effect, an empty cue) sits between them.
    """
    groups: list[list[int]] = []
    current: list[int] = []
    for index, cue in enumerate(cues):
        if not cue.translatable:
            if current:
                groups.append(current)
                current = []
            continue
        if current:
            previous = cues[current[-1]]
            gap = cue.start_ms - previous.end_ms
            if gap > SRT_PARAGRAPH_GAP_MS or len(current) >= MAX_CUES_PER_SENTENCE:
                groups.append(current)
                current = []
        current.append(index)
        if is_sentence_terminal(cue.text) or len(current) >= MAX_CUES_PER_SENTENCE:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def split_translation(text: str, weights: Sequence[int]) -> list[str]:
    """Split ``text`` into ``len(weights)`` pieces by each weight's share of the length.

    Cut points snap to nearby whitespace or punctuation. Scripts without spaces
    are cut by character. Every piece is non-empty when ``text`` has at least
    one non-space character per piece; a shorter translation repeats the last
    non-empty piece so no cue is left blank.
    """
    count = len(weights)
    if count == 0:
        return []
    body = text.strip()
    if count == 1:
        return [body]
    if not body:
        return [""] * count

    pieces = _snap_pieces(body, weights)
    non_space = sum(1 for char in body if not char.isspace())
    if non_space >= count and not all(pieces):
        pieces = _redistribute(body, count)
    if non_space < count:
        pieces = _repeat_short(pieces, body)
    return pieces


def split_long_line(text: str) -> list[str]:
    """Break one translated line into subtitle-sized cues.

    A line with spaces is capped at 84 characters (about two subtitle lines).
    A line without spaces is capped at 32 characters. Cuts prefer sentence
    punctuation, then commas, then whitespace, then a hard character cut.
    """
    remaining = " ".join(text.split())
    if not remaining:
        return []
    limit = 84 if any(char.isspace() for char in remaining) else 32
    if len(remaining) <= limit:
        return [remaining]
    parts: list[str] = []
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = _preferred_cut(window)
        if cut <= 0:
            cut = limit
        piece = remaining[:cut].strip()
        remaining = remaining[cut:].strip()
        if piece:
            parts.append(piece)
    if remaining:
        parts.append(remaining)
    return parts


def assign_cue_texts(
    cues: Sequence[CueSlot],
    groups: Sequence[Sequence[int]],
    translations: Sequence[str],
) -> list[str]:
    """Map one translation per group back onto cue slots. Other cues keep ``raw``."""
    if len(groups) != len(translations):
        raise ValueError("translation count does not match sentence groups")
    texts = [cue.raw for cue in cues]
    for group, translated in zip(groups, translations, strict=True):
        weights = [len(cues[index].text) for index in group]
        pieces = split_translation(translated, weights)
        for index, piece in zip(group, pieces, strict=True):
            texts[index] = piece
    return texts


def _snap_pieces(body: str, weights: Sequence[int]) -> list[str]:
    count = len(weights)
    length = len(body)
    total = sum(weight for weight in weights if weight > 0) or count
    average = length / count
    window = average * 0.4
    cuts: list[int] = []
    consumed = 0
    previous = 0
    positive = [weight if weight > 0 else 0 for weight in weights]
    if sum(positive) == 0:
        positive = [1] * count
        total = count
    for weight in positive[:-1]:
        consumed += weight
        target = consumed / total * length
        cut = _snap_cut(body, target, window, previous)
        cuts.append(cut)
        previous = cut
    return _slices(body, cuts)


def _snap_cut(body: str, target: float, window: float, lower: int) -> int:
    length = len(body)
    lo = max(lower + 1, math.floor(target - window))
    hi = min(length - 1, math.ceil(target + window))
    best: int | None = None
    best_distance = float("inf")
    for cut in range(lo, hi + 1):
        if cut <= lower or cut >= length:
            continue
        before = body[cut - 1]
        after = body[cut]
        if before.isspace() or after.isspace() or before in _SNAP_PUNCTUATION:
            distance = abs(cut - target)
            if distance < best_distance:
                best = cut
                best_distance = distance
    if best is not None:
        return best
    raw = int(round(target))
    return min(max(raw, lower + 1), length)


def _slices(body: str, cuts: list[int]) -> list[str]:
    pieces: list[str] = []
    start = 0
    for cut in cuts:
        pieces.append(body[start:cut].strip())
        start = cut
    pieces.append(body[start:].strip())
    return pieces


def _redistribute(body: str, count: int) -> list[str]:
    content = [index for index, char in enumerate(body) if not char.isspace()]
    base, extra = divmod(len(content), count)
    counts = [base + (1 if index < extra else 0) for index in range(count)]
    pieces: list[str] = []
    cursor = 0
    for index, size in enumerate(counts):
        start = content[cursor]
        last = content[cursor + size - 1]
        if index == count - 1:
            pieces.append(body[start:].strip())
        else:
            next_start = content[cursor + size]
            cut = last + 1
            for pos in range(last + 1, next_start):
                if body[pos].isspace():
                    cut = pos
                    break
            pieces.append(body[start:cut].strip())
        cursor += size
    return pieces


def _repeat_short(pieces: list[str], body: str) -> list[str]:
    filled = list(pieces)
    last = ""
    for index, piece in enumerate(filled):
        if piece:
            last = piece
        elif last:
            filled[index] = last
    first = next((piece for piece in filled if piece), body)
    for index, piece in enumerate(filled):
        if piece:
            break
        filled[index] = first
    return filled


def _preferred_cut(window: str) -> int:
    for charset in (_SENTENCE_PUNCTUATION, _COMMA_PUNCTUATION, None):
        best = -1
        for index, char in enumerate(window):
            if charset is None:
                if char.isspace():
                    best = index
            elif char in charset:
                best = index + 1
        if best > 0:
            return best
    return -1
