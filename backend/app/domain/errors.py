from enum import StrEnum


class ErrorType(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    TRANSLATION_FAILED = "TRANSLATION_FAILED"
    TTS_FAILED = "TTS_FAILED"
    AUDIO_PROCESSING_FAILED = "AUDIO_PROCESSING_FAILED"
    STORAGE_FAILED = "STORAGE_FAILED"
    PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
    TIMEOUT = "TIMEOUT"


class DomainError(Exception):
    """User-facing domain failure with a stable error_type code."""

    def __init__(self, error_type: ErrorType, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
