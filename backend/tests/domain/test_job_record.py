import pytest

from app.domain.audio import ensure_valid_speed
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat


def test_speed_bounds() -> None:
    ensure_valid_speed(0.5)
    ensure_valid_speed(1.0)
    ensure_valid_speed(2.0)
    with pytest.raises(DomainError) as exc:
        ensure_valid_speed(0.49)
    assert exc.value.error_type is ErrorType.INVALID_INPUT
    with pytest.raises(DomainError):
        ensure_valid_speed(2.01)


def test_job_dict_roundtrip() -> None:
    job = Job(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="ko-KR",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )
    assert Job.from_dict(job.to_dict()) == job
