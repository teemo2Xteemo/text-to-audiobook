from collections.abc import Sequence
from pathlib import Path
from shutil import copyfile

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, OutputFormat
from app.domain.languages import LanguageDetection


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def save(self, job: Job) -> None:
        self.jobs[job.id] = job

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def delete(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)


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


class FakeTranslationProvider:
    def __init__(
        self,
        languages: Sequence[str],
        *,
        error: Exception | None = None,
    ) -> None:
        self._languages = list(languages)
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def supported_languages(self) -> Sequence[str]:
        return list(self._languages)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        self.calls.append((text, source_language, target_language))
        if self.error is not None:
            raise self.error
        return f"[{target_language}] {text}"


class FakeNarrationProcessor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def process(self, text: str, language: str) -> str:
        self.calls.append((text, language))
        return text


class FakeTTSProvider:
    def __init__(self, voices: Sequence[Voice], output_dir: Path) -> None:
        self._voices = list(voices)
        self._output_dir = output_dir
        self.calls: list[tuple[str, str, str, TTSSettings]] = []
        self._count = 0

    def voices_for(self, language: str) -> Sequence[Voice]:
        return [voice for voice in self._voices if voice.language == language]

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        self.calls.append((text, language, voice, settings))
        self._count += 1
        self._output_dir.mkdir(parents=True, exist_ok=True)
        path = self._output_dir / f"synth-{self._count:03d}.bin"
        path.write_bytes(b"FAKEAUDIO")
        return AudioArtifact(path=path)


class FakeAudioProcessor:
    def __init__(self) -> None:
        self.normalize_calls: list[tuple[Path, Path]] = []
        self.merge_calls: list[list[Path]] = []

    async def normalize(
        self,
        source: Path,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        del output_format, bitrate_kbps
        self.normalize_calls.append((source, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        copyfile(source, destination)
        return AudioArtifact(path=destination)

    async def merge(
        self,
        sources: Sequence[Path],
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        del output_format, bitrate_kbps
        self.merge_calls.append(list(sources))
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            for source in sources:
                handle.write(source.read_bytes())
        return AudioArtifact(path=destination)
