from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    TRANSLATING = "translating"
    PREPARING_TTS = "preparing_tts"
    GENERATING_AUDIO = "generating_audio"
    MERGING = "merging"
    COMPLETED = "completed"
    FAILED = "failed"


class IllegalJobTransition(Exception):
    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"illegal job transition: {current.value} -> {target.value}")


_FORWARD: dict[JobStatus, JobStatus] = {
    JobStatus.QUEUED: JobStatus.PARSING,
    JobStatus.PARSING: JobStatus.TRANSLATING,
    JobStatus.TRANSLATING: JobStatus.PREPARING_TTS,
    JobStatus.PREPARING_TTS: JobStatus.GENERATING_AUDIO,
    JobStatus.GENERATING_AUDIO: JobStatus.MERGING,
    JobStatus.MERGING: JobStatus.COMPLETED,
}

_TERMINAL = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    if current in _TERMINAL:
        return False
    if target is JobStatus.FAILED:
        return True
    return _FORWARD.get(current) is target


def assert_legal_transition(current: JobStatus, target: JobStatus) -> None:
    if not can_transition(current, target):
        raise IllegalJobTransition(current, target)
