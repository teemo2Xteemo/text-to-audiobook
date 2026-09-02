from app.domain.chunking import CHUNK_MAX_CHARS, Chunk, chunk_text
from app.domain.errors import DomainError, ErrorType


def chunk_source(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[Chunk]:
    chunks = chunk_text(text, max_chars=max_chars)
    if not chunks:
        raise DomainError(ErrorType.INVALID_INPUT, "text produced no chunks")
    return chunks
