from collections.abc import Sequence
from pathlib import Path
from shutil import copyfile

from app.domain.audio import AudioArtifact
from app.domain.jobs import OutputFormat


class FakeAudioProcessor:
    """Pass-through normalize/merge for fake TTS bytes (no FFmpeg)."""

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
