from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.jobs import Job, OutputFormat
from app.domain.languages import LanguageDetection


class TranslationProvider(Protocol):
    async def translate(self, text: str, source_language: str, target_language: str) -> str: ...

    def supported_languages(self) -> Sequence[str]: ...


class TTSProvider(Protocol):
    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact: ...

    def voices_for(self, language: str) -> Sequence[Voice]: ...


class NarrationProcessor(Protocol):
    def process(self, text: str, language: str) -> str: ...


class LanguageDetector(Protocol):
    async def detect(self, text: str) -> LanguageDetection: ...


class JobStore(Protocol):
    async def save(self, job: Job) -> None: ...

    async def get(self, job_id: str) -> Job | None: ...

    async def delete(self, job_id: str) -> None: ...

    async def list_ids(self) -> Sequence[str]: ...


class JobQueue(Protocol):
    async def enqueue(self, job_id: str) -> None: ...


class SourceTextStorage(Protocol):
    async def write_source(self, job_id: str, text: str) -> None: ...

    async def read_source(self, job_id: str) -> str: ...

    async def delete_job(self, job_id: str) -> None: ...


class AudioProcessor(Protocol):
    async def normalize(
        self,
        source: Path,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact: ...

    async def merge(
        self,
        sources: Sequence[Path],
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact: ...
