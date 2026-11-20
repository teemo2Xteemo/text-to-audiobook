"""Pack single-line translation units into provider batches."""

from __future__ import annotations

from collections.abc import Sequence

BATCH_SEPARATOR = "\n"
_MIN_DURATION_MS = 1000
_MAX_DURATION_MS = 7000


def join_batch(units: Sequence[str]) -> str:
    return BATCH_SEPARATOR.join(units)


def split_batch(translated: str, expected: int) -> list[str] | None:
    """Split a provider response. ``None`` when the line count does not match."""
    lines = [line.strip() for line in translated.strip().split(BATCH_SEPARATOR)]
    lines = [line for line in lines if line]
    if len(lines) != expected:
        return None
    return lines


def pack_batches(units: Sequence[str], max_chars: int) -> list[list[int]]:
    """Consecutive unit indices whose joined length fits ``max_chars``.

    A unit longer than ``max_chars`` is its own batch.
    """
    limit = max(1, max_chars)
    batches: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    for index, unit in enumerate(units):
        extra = len(unit) if not current else len(BATCH_SEPARATOR) + len(unit)
        if current and current_len + extra > limit:
            batches.append(current)
            current = [index]
            current_len = len(unit)
        else:
            current.append(index)
            current_len += extra
    if current:
        batches.append(current)
    return batches


def estimate_duration_ms(text: str) -> int:
    """Rough cue length for text that has no source timing. Clamped to 1–7 s."""
    per_char = 70 if any(char.isspace() for char in text) else 200
    return max(_MIN_DURATION_MS, min(_MAX_DURATION_MS, len(text) * per_char))
