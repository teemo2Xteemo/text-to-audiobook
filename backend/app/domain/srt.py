from dataclasses import dataclass


@dataclass(frozen=True)
class SRTCue:
    index: int
    start_ms: int
    end_ms: int
    text: str

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass(frozen=True)
class TimedChunk:
    chunk_index: int
    start_ms: int
    end_ms: int
    text: str
    cue_indices: list[int]

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)
