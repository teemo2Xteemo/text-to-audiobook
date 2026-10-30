from dataclasses import dataclass

CHUNK_MAX_CHARS = 1200
PARAGRAPH_SEPARATOR = "\n\n"
SENTENCE_TERMINALS = frozenset(".?!。！？．｡…")


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    index: int


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    packed = _pack_paragraphs(normalized, max_chars)
    return [
        Chunk(id=_chunk_id(index), text=part, index=index)
        for index, part in enumerate(packed, start=1)
    ]


def _chunk_id(index: int) -> str:
    return f"chunk-{index:03d}"


def _pack_paragraphs(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    buf = ""
    paragraphs = [part for part in text.split(PARAGRAPH_SEPARATOR) if part]
    for paragraph in paragraphs:
        units = _atomic_units(paragraph, max_chars)
        first_unit = True
        for unit in units:
            sep = PARAGRAPH_SEPARATOR if buf and first_unit else ""
            first_unit = False
            if not buf:
                buf = unit
                continue
            if len(buf) + len(sep) + len(unit) <= max_chars:
                buf = buf + sep + unit
            else:
                chunks.append(buf)
                buf = unit
    if buf:
        chunks.append(buf)
    return chunks


def atomic_units(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    units: list[str] = []
    for sentence in _split_sentences(paragraph):
        if not sentence:
            continue
        if len(sentence) <= max_chars:
            units.append(sentence)
        else:
            units.extend(_hard_split(sentence, max_chars))
    return units


_atomic_units = atomic_units


def _split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    buf: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        buf.append(text[i])
        if text[i] in SENTENCE_TERMINALS:
            while i + 1 < length and text[i + 1] in SENTENCE_TERMINALS:
                i += 1
                buf.append(text[i])
            sentences.append("".join(buf))
            buf = []
        i += 1
    if buf:
        sentences.append("".join(buf))
    return sentences


CLAUSE_SEPARATORS = frozenset(",;:，；：、")


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split an oversize sentence without cutting words.

    Cuts after the last clause separator in the back half of the window, else after the
    last whitespace, else at ``max_chars`` (scripts without spaces). Concatenating the
    parts reproduces ``text`` exactly.
    """
    parts: list[str] = []
    rest = text
    while len(rest) > max_chars:
        cut = _soft_cut(rest[:max_chars], max_chars)
        parts.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        parts.append(rest)
    return parts


def _soft_cut(window: str, max_chars: int) -> int:
    clause = max((i for i, ch in enumerate(window) if ch in CLAUSE_SEPARATORS), default=-1)
    if clause >= max_chars // 2:
        return _after_spaces(window, clause + 1)
    space = max((i for i, ch in enumerate(window) if ch.isspace()), default=-1)
    if space > 0:
        return space + 1
    return max_chars


def _after_spaces(window: str, index: int) -> int:
    while index < len(window) and window[index].isspace():
        index += 1
    return index
