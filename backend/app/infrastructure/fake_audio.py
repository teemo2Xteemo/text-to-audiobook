from collections.abc import Sequence
from pathlib import Path
from shutil import copyfile

from app.domain.audio import AudioArtifact
from app.domain.jobs import OutputFormat


class FakeAudioProcessor:
    """Pass-through normalize/merge for fake TTS bytes (no FFmpeg)."""

    def __init__(self) -> None:
        self.normalize_calls: list[tuple[Path, Path]] = []
        self.full_encode_flags: list[bool] = []
        self.merge_calls: list[list[Path]] = []
        self.silence_calls: list[tuple[int, Path]] = []
        self.tempo_calls: list[tuple[Path, Path, float]] = []
        self.durations: dict[str, int] = {}
        self.default_duration_ms: int = 1000

    def set_duration(self, path: Path | str, duration_ms: int) -> None:
        self.durations[str(path)] = duration_ms
        self.durations[Path(path).name] = duration_ms

    async def get_duration_ms(self, path: Path) -> int:
        if str(path) in self.durations:
            return self.durations[str(path)]
        if str(path.resolve()) in self.durations:
            return self.durations[str(path.resolve())]
        if path.name in self.durations:
            return self.durations[path.name]
        return self.default_duration_ms

    async def generate_silence(
        self,
        duration_ms: int,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        del output_format, bitrate_kbps
        self.silence_calls.append((duration_ms, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(f"FAKE_SILENCE_{duration_ms}ms".encode())
        self.set_duration(destination, duration_ms)
        return AudioArtifact(path=destination)

    async def adjust_tempo(
        self,
        source: Path,
        destination: Path,
        factor: float,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
    ) -> AudioArtifact:
        del output_format, bitrate_kbps
        self.tempo_calls.append((source, destination, factor))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            copyfile(source, destination)
        else:
            destination.write_bytes(b"FAKE_TEMPO_AUDIO")
        source_duration = await self.get_duration_ms(source)
        adjusted = int(round(source_duration / factor)) if factor > 0 else source_duration
        self.set_duration(destination, adjusted)
        return AudioArtifact(path=destination)

    async def normalize(
        self,
        source: Path,
        destination: Path,
        *,
        output_format: OutputFormat,
        bitrate_kbps: int,
        full_encode: bool = False,
    ) -> AudioArtifact:
        del output_format, bitrate_kbps
        self.normalize_calls.append((source, destination))
        self.full_encode_flags.append(full_encode)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            copyfile(source, destination)
        else:
            destination.write_bytes(f"FAKE_NORMALIZED_{destination.name}".encode())
        if str(destination) not in self.durations and destination.name not in self.durations:
            source_dur = await self.get_duration_ms(source)
            self.set_duration(destination, source_dur)
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
