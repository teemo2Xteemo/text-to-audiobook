import pytest

from app.domain.jobs import (
    IllegalJobTransition,
    JobStatus,
    assert_legal_transition,
    can_transition,
)

PIPELINE = [
    JobStatus.QUEUED,
    JobStatus.PARSING,
    JobStatus.TRANSLATING,
    JobStatus.PREPARING_TTS,
    JobStatus.GENERATING_AUDIO,
    JobStatus.MERGING,
    JobStatus.COMPLETED,
]


def test_job_status_values_are_lowercase() -> None:
    assert JobStatus.QUEUED == "queued"
    assert JobStatus.PARSING == "parsing"
    assert JobStatus.TRANSLATING == "translating"
    assert JobStatus.PREPARING_TTS == "preparing_tts"
    assert JobStatus.GENERATING_AUDIO == "generating_audio"
    assert JobStatus.MERGING == "merging"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"


def test_legal_forward_transitions() -> None:
    for current, nxt in zip(PIPELINE, PIPELINE[1:], strict=False):
        assert can_transition(current, nxt)
        assert_legal_transition(current, nxt)


def test_any_non_terminal_may_fail() -> None:
    for status in PIPELINE[:-1]:
        assert can_transition(status, JobStatus.FAILED)
        assert_legal_transition(status, JobStatus.FAILED)


def test_skip_and_reverse_are_illegal() -> None:
    with pytest.raises(IllegalJobTransition):
        assert_legal_transition(JobStatus.QUEUED, JobStatus.TRANSLATING)
    with pytest.raises(IllegalJobTransition):
        assert_legal_transition(JobStatus.PARSING, JobStatus.QUEUED)
    assert not can_transition(JobStatus.QUEUED, JobStatus.QUEUED)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (JobStatus.COMPLETED, JobStatus.FAILED),
        (JobStatus.COMPLETED, JobStatus.QUEUED),
        (JobStatus.COMPLETED, JobStatus.COMPLETED),
        (JobStatus.FAILED, JobStatus.QUEUED),
        (JobStatus.FAILED, JobStatus.FAILED),
        (JobStatus.FAILED, JobStatus.PARSING),
    ],
)
def test_terminal_states_cannot_transition(current: JobStatus, target: JobStatus) -> None:
    assert not can_transition(current, target)
    with pytest.raises(IllegalJobTransition) as exc:
        assert_legal_transition(current, target)
    assert exc.value.current is current
    assert exc.value.target is target
