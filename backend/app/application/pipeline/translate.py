from app.domain.errors import DomainError, ErrorType
from app.domain.ports import TranslationProvider


async def translate_chunk(
    text: str,
    *,
    source_language: str,
    target_language: str,
    provider: TranslationProvider,
) -> str:
    supported = set(provider.supported_languages())
    if source_language not in supported or target_language not in supported:
        raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "language is not supported")
    try:
        return await provider.translate(text, source_language, target_language)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorType.TRANSLATION_FAILED, "translation failed") from exc
