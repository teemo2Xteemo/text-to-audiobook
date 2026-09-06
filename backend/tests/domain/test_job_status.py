import pytest

from app.domain.jobs import (
    IllegalJobTransition,
    JobStatus,
    assert_legal_transition,
    can_transition,
    is_at_or_past,
    is_terminal,
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


def test_failed_to_queued_is_legal() -> None:
    assert can_transition(JobStatus.FAILED, JobStatus.QUEUED)
    assert_legal_transition(JobStatus.FAILED, JobStatus.QUEUED)


def test_is_terminal() -> None:
    assert is_terminal(JobStatus.COMPLETED)
    assert is_terminal(JobStatus.FAILED)
    assert not is_terminal(JobStatus.QUEUED)
    assert not is_terminal(JobStatus.GENERATING_AUDIO)


def test_is_at_or_past_forward_pipeline() -> None:
    assert is_at_or_past(JobStatus.GENERATING_AUDIO, JobStatus.PARSING)
    assert is_at_or_past(JobStatus.GENERATING_AUDIO, JobStatus.GENERATING_AUDIO)
    assert not is_at_or_past(JobStatus.GENERATING_AUDIO, JobStatus.MERGING)
    assert not is_at_or_past(JobStatus.QUEUED, JobStatus.PARSING)
    assert not is_at_or_past(JobStatus.FAILED, JobStatus.PARSING)
    assert not is_at_or_past(JobStatus.TRANSLATING, JobStatus.FAILED)
