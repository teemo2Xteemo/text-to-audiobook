from app.domain.errors import DomainError, ErrorType
from app.domain.languages import resolve_source_language
from app.domain.ports import LanguageDetector


async def parse_source(
    text: str, source_language: str, detector: LanguageDetector
) -> tuple[str, str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise DomainError(ErrorType.INVALID_INPUT, "text is required")
    resolved = await resolve_source_language(normalized, source_language, detector)
    return normalized, resolved
