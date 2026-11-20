from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DocumentFormat(StrEnum):
    TXT = "txt"
    SRT = "srt"
    VTT = "vtt"
    ASS = "ass"


SUBTITLE_FORMATS = frozenset({DocumentFormat.SRT, DocumentFormat.VTT, DocumentFormat.ASS})


@dataclass(frozen=True)
class CueSlot:
    """One timed cue inside a lossless subtitle document.

    ``text`` is the plain string sent to translation. ``raw`` is the original
    text field, rendered unchanged when the cue is not translated. ``prefix``
    is a leading ``{...}`` override (for example ``{\\an8}``) reattached after
    translation.
    """

    start_ms: int
    end_ms: int
    text: str
    raw: str
    prefix: str = ""
    translatable: bool = True


@dataclass(frozen=True)
class SubtitleDocument:
    """Subtitle file as interleaved raw strings and cue slots.

    Concatenating raw parts with each cue's original ``raw`` reproduces the
    normalised source. Only a cue's text field is replaced on render.
    """

    format: DocumentFormat
    parts: tuple[str | CueSlot, ...]

    @property
    def cues(self) -> list[CueSlot]:
        return [part for part in self.parts if isinstance(part, CueSlot)]
