from collections.abc import Sequence
from pathlib import Path

from app.domain.audio import AudioArtifact
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import OutputFormat
from app.domain.ports import AudioProcessor


async def merge_artifacts(
    audio: AudioProcessor,
    sources: Sequence[Path],
    destination: Path,
    *,
    output_format: OutputFormat,
    bitrate_kbps: int,
) -> AudioArtifact:
    try:
        return await audio.merge(
            sources,
            destination,
            output_format=output_format,
            bitrate_kbps=bitrate_kbps,
        )
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "audio merge failed") from exc
