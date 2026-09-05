from app.domain.languages import LanguageDetection


class PassthroughNarrationProcessor:
    def process(self, text: str, language: str) -> str:
        del language
        return text


class FixedLanguageDetector:
    def __init__(self, language_code: str = "en-US", confidence: float = 0.92) -> None:
        self.language_code = language_code
        self.confidence = confidence

    async def detect(self, text: str) -> LanguageDetection:
        del text
        return LanguageDetection(language_code=self.language_code, confidence=self.confidence)
