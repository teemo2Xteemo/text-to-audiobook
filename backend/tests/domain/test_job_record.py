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
    with pytest.raises(DomainError):
        ensure_valid_speed(float("nan"))
    with pytest.raises(DomainError):
        ensure_valid_speed(float("inf"))
    with pytest.raises(DomainError):
        ensure_valid_speed(float("-inf"))


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
    assert job.translation_provider is None
    assert job.translation_model is None
    assert "translation_provider" not in job.to_dict()


def _legacy_job_payload() -> dict[str, object]:
    return {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "status": "queued",
        "source_language": "en-US",
        "target_language": "ko-KR",
        "voice": None,
        "speed": 1.0,
        "output_format": "mp3",
        "output_bitrate_kbps": 128,
    }


def test_pin_roundtrip_stores_both_fields() -> None:
    job = Job(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="ko-KR",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
        translation_provider="ollama",
        translation_model="translategemma:4b",
    )
    restored = Job.from_dict(job.to_dict())
    assert restored == job
    assert restored.translation_provider == "ollama"
    assert restored.translation_model == "translategemma:4b"


def test_missing_pin_keys_load_as_unpinned() -> None:
    job = Job.from_dict(_legacy_job_payload())
    assert job.translation_provider is None
    assert job.translation_model is None


@pytest.mark.parametrize("key", ["translation_provider", "translation_model"])
def test_half_pin_is_treated_as_missing(key: str) -> None:
    payload = _legacy_job_payload()
    payload[key] = "ollama" if key == "translation_provider" else "translategemma:4b"
    job = Job.from_dict(payload)
    assert job.translation_provider is None
    assert job.translation_model is None


@pytest.mark.parametrize("provider", ["fake", "nllb", "ollama", "gemini", "groq", "openrouter"])
def test_all_registered_pin_providers_accepted(provider: str) -> None:
    payload = _legacy_job_payload()
    payload["translation_provider"] = provider
    payload["translation_model"] = "test-model"
    job = Job.from_dict(payload)
    assert job.translation_provider == provider
    assert job.translation_model == "test-model"


def test_pin_provider_must_be_registered() -> None:
    payload = _legacy_job_payload()
    payload["translation_provider"] = "libre"
    payload["translation_model"] = "translategemma:4b"
    with pytest.raises(ValueError, match="translation_provider"):
        Job.from_dict(payload)


def test_pin_model_must_be_non_empty() -> None:
    payload = _legacy_job_payload()
    payload["translation_provider"] = "ollama"
    payload["translation_model"] = "   "
    job = Job.from_dict(payload)
    assert job.translation_provider is None
    assert job.translation_model is None


def test_recovery_attempts_omitted_when_zero_and_roundtrips_when_positive() -> None:
    zero = Job.from_dict(_legacy_job_payload())
    assert zero.recovery_attempts == 0
    assert "recovery_attempts" not in zero.to_dict()

    bumped = Job(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status=JobStatus.TRANSLATING,
        source_language="en-US",
        target_language="ko-KR",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
        recovery_attempts=2,
    )
    assert bumped.to_dict()["recovery_attempts"] == 2
    assert Job.from_dict(bumped.to_dict()) == bumped


def test_negative_recovery_attempts_rejected() -> None:
    payload = _legacy_job_payload()
    payload["recovery_attempts"] = -1
    with pytest.raises(ValueError, match="recovery_attempts"):
        Job.from_dict(payload)
