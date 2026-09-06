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

    async def enqueue(self, job_id: str) -> None:
        self.job_ids.append(job_id)


class FailingQueue:
    async def enqueue(self, job_id: str) -> None:
        raise RuntimeError("queue unavailable")


class FakeLanguageDetector:
    def __init__(self, language_code: str = "en-US", confidence: float = 0.92) -> None:
        self.language_code = language_code
        self.confidence = confidence
        self.calls: list[str] = []

    async def detect(self, text: str) -> LanguageDetection:
        self.calls.append(text)
        return LanguageDetection(language_code=self.language_code, confidence=self.confidence)


class FakeNarrationProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def process(self, text: str, language: str) -> str:
        self.calls.append((text, language))
        return text
