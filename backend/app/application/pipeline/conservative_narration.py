from __future__ import annotations

import re

from app.domain.chunking import PARAGRAPH_SEPARATOR, SENTENCE_TERMINALS

_ELLIPSIS = "..."
_DASH = " — "
_ELLIPSIS_MARK = "\ufffc"
_ELLIPSIS_RE = re.compile(r"\.{3,}|…+")
_SAME_BANG_RE = re.compile(r"!{2,}")
_SAME_Q_RE = re.compile(r"\?{2,}")
_SAME_FULLWIDTH_BANG_RE = re.compile(r"！{2,}")
_SAME_FULLWIDTH_Q_RE = re.compile(r"？{2,}")
_DASH_RE = re.compile(r"[—–―‒−]|-{2,}")
_HORIZONTAL_SPACE_RE = re.compile(r"[ \t\f\v]+")
_NEWLINE_SPACE_RE = re.compile(r" *\n *")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_QUOTE_TABLE = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "«": '"',
        "»": '"',
        "「": '"',
        "」": '"',
        "『": '"',
        "』": '"',
        "｢": '"',
        "｣": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "‹": "'",
        "›": "'",
    }
)


class ConservativeNarrationProcessor:
    """Meaning-preserving punctuation and pause structure for TTS input."""

    def process(self, text: str, language: str) -> str:
        del language
        return _narrate(text)


def _narrate(text: str) -> str:
    normalized = _normalize(text)
    if not normalized:
        return ""
    units = [
        stripped
        for paragraph in normalized.split(PARAGRAPH_SEPARATOR)
        for unit in _split_sentences(paragraph.replace("\n", " "))
        if (stripped := unit.strip())
    ]
    return PARAGRAPH_SEPARATOR.join(units)


def _normalize(text: str) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = value.translate(_QUOTE_TABLE)
    value = _ELLIPSIS_RE.sub(_ELLIPSIS_MARK, value)
    value = _DASH_RE.sub(_DASH, value)
    value = _SAME_BANG_RE.sub("!", value)
    value = _SAME_Q_RE.sub("?", value)
    value = _SAME_FULLWIDTH_BANG_RE.sub("！", value)
    value = _SAME_FULLWIDTH_Q_RE.sub("？", value)
    value = _collapse_terminal_runs(value)
    value = value.replace(_ELLIPSIS_MARK, _ELLIPSIS)
    value = _HORIZONTAL_SPACE_RE.sub(" ", value)
    value = _NEWLINE_SPACE_RE.sub("\n", value)
    value = _MULTI_NEWLINE_RE.sub("\n\n", value)
    value = _MULTI_SPACE_RE.sub(" ", value)
    return value.strip()


def _collapse_terminal_runs(text: str) -> str:
    chars: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == _ELLIPSIS_MARK or ch not in SENTENCE_TERMINALS:
            chars.append(ch)
            i += 1
            continue
        chars.append(ch)
        i += 1
        while i < length and text[i] in SENTENCE_TERMINALS and text[i] != _ELLIPSIS_MARK:
            i += 1
    return "".join(chars)


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    buf: list[str] = []
    in_double_quote = False
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == '"':
            in_double_quote = not in_double_quote
            buf.append(ch)
            i += 1
            continue
        buf.append(ch)
        if ch in SENTENCE_TERMINALS:
            while i + 1 < length and text[i + 1] in SENTENCE_TERMINALS:
                i += 1
                buf.append(text[i])
            if i + 1 < length and text[i + 1] == '"':
                i += 1
                buf.append('"')
                in_double_quote = False
            if not in_double_quote:
                sentences.append("".join(buf))
                buf = []
        i += 1
    if buf:
        sentences.append("".join(buf))
    return sentences
