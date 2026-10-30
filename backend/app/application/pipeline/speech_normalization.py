"""Fidelity checks for LLM speech normalization output.

The normalizer may only spell out numbers, dates and abbreviations. Chat models sometimes add a
preamble, drop sentences, change line structure or answer in another script; such output must
never reach TTS or the cache. Checks are script- and language-agnostic.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["normalization_problem"]

# Spelled-out numbers grow the text; losing letters means dropped content.
_MIN_LETTER_RATIO = 0.8
_MAX_LETTER_RATIO = 3.0
# Below this many letters a single spelled-out number skews the ratio too much to judge.
_MIN_LETTERS_FOR_RATIO = 40
_WORD_RE = re.compile(r"\w+")


def normalization_problem(original: str, normalized: str) -> str | None:
    """Return why ``normalized`` is not a faithful spoken rewrite of ``original``, else ``None``."""
    if not normalized.strip():
        return "empty output"
    if _digits(normalized) > _digits(original):
        return "more digits than the input"
    if _line_count(normalized) != _line_count(original):
        return "line structure changed"
    original_letters = _letters(original)
    new_scripts = _scripts(normalized) - _scripts(original)
    if original_letters and new_scripts:
        return f"new script in output: {', '.join(sorted(new_scripts))}"
    if original_letters >= _MIN_LETTERS_FOR_RATIO:
        ratio = _letters(normalized) / original_letters
        if not _MIN_LETTER_RATIO <= ratio <= _MAX_LETTER_RATIO:
            return f"letter ratio {ratio:.2f} outside {_MIN_LETTER_RATIO}-{_MAX_LETTER_RATIO}"
    if not _keeps_opening(original, normalized):
        return "opening words changed (preamble or rewrite)"
    return None


def _digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def _line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _scripts(text: str) -> set[str]:
    # First word of the Unicode name: LATIN, CJK, HANGUL, CYRILLIC, HIRAGANA, ...
    return {unicodedata.name(ch, "?").split(" ")[0] for ch in text if ch.isalpha()}


def _keeps_opening(original: str, normalized: str) -> bool:
    expected = [
        token.casefold()
        for token in _WORD_RE.findall(original)
        if not any(ch.isdigit() for ch in token)
    ][:2]
    if not expected:
        return True
    opening = [token.casefold() for token in _WORD_RE.findall(normalized)][:3]
    return any(token in opening for token in expected)
