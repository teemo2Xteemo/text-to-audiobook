from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, OutputFormat
from app.domain.languages import LanguageDetection


class TranslationProvider(Protocol):
    async def translate(self, text: str, source_language: str, target_language: str) -> str: ...

    def supported_languages(self) -> Sequence[str]: ...


class NullTranslationProvider(TranslationProvider):
    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        raise DomainError(
            ErrorType.PROVIDER_CONFIGURATION_ERROR,
            "translation is not supported in narrate mode",
        )

    def supported_languages(self) -> Sequence[str]:
        return ()

    def languages(self) -> set[str]:
        return set()


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


class DiskJobStore(Protocol):
    """Direct on-disk ``status.json`` access for crash recovery and retry FSM transitions."""

    async def save_job(self, job: Job) -> None: ...

    async def get_job(self, job_id: str) -> Job | None: ...

    async def list_job_ids(self) -> Sequence[str]: ...


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
        full_encode: bool = False,
    ) -> AudioArtifact: ...

    async def merge(
        self,
        sources: Sequence[Path],
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact: ...

    async def get_duration_ms(self, path: Path) -> int: ...

    async def generate_silence(
        self,
        duration_ms: int,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact: ...

    async def adjust_tempo(
        self,
        source: Path,
        destination: Path,
        factor: float,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact: ...


class ArtifactCache(Protocol):
    """Content-addressed translation/TTS blobs. Not a job-status GET cache."""

    def get(self, operation: str, key: str, destination: Path) -> bool: ...

    def put(self, operation: str, key: str, source: Path) -> None: ...


class CheckpointStorage(Protocol):
    """Workspace-scoped ``checkpoint.json`` storage port."""

    def load_raw(self) -> list[Any]: ...

    def save_raw(self, entries: list[dict[str, str]]) -> None: ...

    def relative(self, artifact: Path) -> str: ...

    def resolve(self, artifact_path: str) -> Path: ...
