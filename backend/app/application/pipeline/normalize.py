from pathlib import Path

from app.domain.audio import AudioArtifact
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat
from app.domain.ports import AudioProcessor


async def normalize_chunk(
    audio: AudioProcessor,
    source: Path,
    destination: Path,
    *,
    output_format: OutputFormat,
    bitrate_kbps: int,
) -> AudioArtifact:
    try:
        return await audio.normalize(
            source,
            destination,
            output_format=output_format,
            bitrate_kbps=bitrate_kbps,
        )
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "audio normalize failed") from exc
