from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job
from app.domain.languages import LanguageDetection
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.tts.fake import FakeTTSProvider

__all__ = [
    "FailingQueue",
    "FakeAudioProcessor",
    "FakeLanguageDetector",
    "FakeNarrationProcessor",
    "FakeTTSProvider",
    "FakeTranslationProvider",
    "MergingLinesTranslator",
    "InMemoryJobStore",
    "InMemoryQueue",
    "InMemorySourceStorage",
]


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)

    async def list_ids(self) -> list[str]:
        return sorted(self.jobs.keys())


class InMemorySourceStorage:
    def __init__(self) -> None:
        self.texts: dict[str, str] = {}

    async def write_source(self, job_id: str, text: str) -> None:
        self.texts[job_id] = text

    async def read_source(self, job_id: str) -> str:
        try:
            return self.texts[job_id]
        except KeyError as exc:
            raise DomainError(ErrorType.STORAGE_FAILED, "source text not found") from exc

    async def delete_job(self, job_id: str) -> None:
        self.texts.pop(job_id, None)


class InMemoryQueue:
    def __init__(self) -> None:
        self.job_ids: list[str] = []

    async def enqueue(self, job_id: str, **_kwargs: object) -> None:
        self.job_ids.append(job_id)


class FailingQueue:
    async def enqueue(self, job_id: str, **_kwargs: object) -> None:
        del job_id
        raise RuntimeError("queue unavailable")


class FakeLanguageDetector:
    def __init__(self, language_code: str = "en-US", confidence: float = 0.92) -> None:
        self.language_code = language_code
        self.confidence = confidence
        self.calls: list[str] = []

    async def detect(self, text: str) -> LanguageDetection:
        self.calls.append(text)
        return LanguageDetection(language_code=self.language_code, confidence=self.confidence)

    async def detect_candidates(self, text: str) -> list[tuple[str, float]]:
        self.calls.append(text)
        return [(self.language_code, self.confidence)]


class MergingLinesTranslator:
    """Multi-line input collapses to one line so the batch count check fails.

    Single-line input is tagged with the source text, which makes cross-sentence
    drift visible. ``fail_on`` raises once when that exact line is translated.
    """

    def __init__(self, languages: list[str], *, fail_on: str | None = None) -> None:
        self._languages = list(languages)
        self.fail_on = fail_on
        self.calls: list[tuple[str, str, str]] = []
        self.single_line_calls = 0
        self._failed = False

    def supported_languages(self) -> list[str]:
        return list(self._languages)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        self.calls.append((text, source_language, target_language))
        if "\n" in text:
            return " ".join(line.strip() for line in text.split("\n") if line.strip())
        self.single_line_calls += 1
        if self.fail_on is not None and text == self.fail_on and not self._failed:
            self._failed = True
            raise RuntimeError("translation failed")
        return f"[{target_language}] {text}"


class FakeNarrationProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def process(self, text: str, language: str) -> str:
        self.calls.append((text, language))
        return text
