from collections.abc import Sequence

# Fixture locales for offline Compose — not domain constants.
_DEFAULT_LANGUAGES: tuple[str, ...] = (
    "en-US",
    "zh-CN",
    "vi-VN",
    "ja-JP",
    "ko-KR",
)


class FakeTranslationProvider:
    """Offline translation stub registered as ``TRANSLATION_PROVIDER=fake``."""

    def __init__(
        self,
        languages: Sequence[str] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._languages = list(languages) if languages is not None else list(_DEFAULT_LANGUAGES)
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def supported_languages(self) -> Sequence[str]:
        return list(self._languages)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        self.calls.append((text, source_language, target_language))
        if self.error is not None:
            raise self.error
        return f"[{target_language}] {text}"
