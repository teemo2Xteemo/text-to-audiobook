from app.domain.audio import AudioArtifact, TTSSettings
from app.domain.errors import DomainError, ErrorType
from app.domain.ports import TTSProvider


async def synthesize_chunk(
    text: str,
    *,
    language: str,
    voice: str | None,
    settings: TTSSettings,
    provider: TTSProvider,
) -> AudioArtifact:
    voices = list(provider.voices_for(language))
    if not voices:
        raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "no voice available for language")
    selected = voices[0].id if voice is None else voice
    if selected not in {item.id for item in voices}:
        raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language")
    try:
        return await provider.synthesize(text, language, selected, settings)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorType.TTS_FAILED, "tts failed") from exc
