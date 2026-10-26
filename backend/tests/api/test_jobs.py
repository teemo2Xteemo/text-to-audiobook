import asyncio
import uuid
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.schemas import JobStatusResponse
from app.application.jobs import JobService
from app.config.settings import Settings
from app.domain.errors import ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.translation_profile import TranslationProfile
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.main import create_app
from tests.fakes import InMemoryJobStore, InMemoryQueue, InMemorySourceStorage

VALID_BODY = {
    "text": "Once upon a time",
    "source_language": "en-US",
    "target_language": "ja-JP",
}


@pytest.fixture
def memory(
    tmp_path: Path,
) -> tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue]:
    store = InMemoryJobStore()
    source = InMemorySourceStorage()
    queue = InMemoryQueue()
    service = JobService(
        jobs=store,
        source_storage=source,
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=FilesystemJobStorage(tmp_path),
        available_profiles=[
            TranslationProfile(
                id="gemini-flash",
                provider="gemini",
                model="gemini-3.5-flash",
                label="Gemini",
                badge="ONLINE",
                description="desc",
                supported_languages=("en-US", "ja-JP"),
            ),
            TranslationProfile(
                id="nllb-600m",
                provider="nllb",
                model="facebook/nllb-200-distilled-600M",
                label="NLLB",
                badge="OFFLINE",
                description="desc",
                supported_languages=("en-US", "vi-VN"),
            ),
        ],
    )
    return service, store, source, queue


@pytest.fixture
def jobs_client(
    tmp_path: Path,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> Generator[TestClient, None, None]:
    service, _, _, _ = memory
    settings = Settings(_env_file=None, storage_path=tmp_path, max_upload_bytes=200)
    app = create_app(settings)
    app.state.job_service = service
    with TestClient(app) as client:
        yield client


def test_post_does_not_accept_a_translator_and_get_returns_lab_pin(
    jobs_client: TestClient,
) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={**VALID_BODY, "translation_provider": "ollama", "translation_model": "other"},
    )
    assert response.status_code == 202
    assert set(response.json()) == {"job_id", "status"}
    job_id = response.json()["job_id"]
    status = jobs_client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["translation_provider"] == "fake"
    assert status.json()["translation_model"] == "fake"


def test_post_with_translation_profile_id_resolves_profile(
    jobs_client: TestClient,
) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={**VALID_BODY, "translation_profile_id": "gemini-flash"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    status = jobs_client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["translation_provider"] == "gemini"
    assert status.json()["translation_model"] == "gemini-3.5-flash"


def test_post_with_unknown_translation_profile_id_fails(
    jobs_client: TestClient,
) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={**VALID_BODY, "translation_profile_id": "non-existent"},
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == "INVALID_INPUT"


def test_post_with_profile_and_unsupported_language_returns_400(
    jobs_client: TestClient,
) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={
            "text": "Hello story",
            "source_language": "en-US",
            "target_language": "ja-JP",
            "translation_profile_id": "nllb-600m",
        },
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == "UNSUPPORTED_LANGUAGE"


def test_post_multipart_with_translation_profile_id_resolves_profile(
    jobs_client: TestClient,
) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={
            "source_language": "en-US",
            "target_language": "ja-JP",
            "translation_profile_id": "gemini-flash",
        },
        files={"file": ("story.txt", b"Once upon a time", "text/plain")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    status = jobs_client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["translation_provider"] == "gemini"
    assert status.json()["translation_model"] == "gemini-3.5-flash"


def test_status_omits_pin_when_the_job_has_none() -> None:
    job = Job(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="ja-JP",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )
    payload = JobStatusResponse.from_job(job).model_dump()
    assert "translation_provider" not in payload
    assert "translation_model" not in payload


def test_post_json_returns_202_queued_uuid(jobs_client: TestClient) -> None:
    response = jobs_client.post("/api/jobs", json=VALID_BODY)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == JobStatus.QUEUED
    uuid.UUID(body["job_id"])


def test_post_then_get_queued_progress(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    response = jobs_client.get(f"/api/jobs/{created['job_id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert body["stage"] == "queued"
    assert body["chunk_current"] == 0
    assert body["chunk_total"] == 0
    assert body["error_type"] is None
    assert body["message"] is None
    assert body["source_language"] == "en-US"
    assert body["target_language"] == "ja-JP"
    assert body["output_format"] == "mp3"
    assert body["speed"] == 1.0
    assert body["audio_url"] is None
    _, _, source, queue = memory
    assert source.texts[created["job_id"]] == "Once upon a time"
    assert queue.job_ids == [created["job_id"]]


def test_post_txt_upload(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "ko-KR", "target_language": "en-US"},
        files={"file": ("story.txt", b"uploaded text", "text/plain")},
    )
    assert response.status_code == 202
    uuid.UUID(response.json()["job_id"])


def test_post_rejects_both_text_and_file(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={
            "text": "pasted",
            "source_language": "en-US",
            "target_language": "ja-JP",
        },
        files={"file": ("story.txt", b"uploaded", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_post_rejects_path_traversal_filename(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "target_language": "ja-JP"},
        files={"file": ("../secret.txt", b"nope", "text/plain")},
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_post_rejects_bad_mime(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "target_language": "ja-JP"},
        files={"file": ("story.txt", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_post_rejects_oversize_text(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={
            "text": "x" * 201,
            "source_language": "en-US",
            "target_language": "ja-JP",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error_type"] == ErrorType.INVALID_INPUT
    assert "message" in body


def test_post_rejects_auto_target(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={
            "text": "story",
            "source_language": "zh-CN",
            "target_language": "auto",
        },
    )
    assert response.status_code == 400
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_get_unknown_job_is_404_envelope(jobs_client: TestClient) -> None:
    job_id = str(uuid.uuid4())
    response = jobs_client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 404
    assert response.json() == {
        "error_type": ErrorType.INVALID_INPUT,
        "message": "job not found",
    }


def test_get_rejects_non_uuid(jobs_client: TestClient) -> None:
    response = jobs_client.get("/api/jobs/not-a-uuid")
    assert response.status_code == 400
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_download_completed_returns_bytes(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    _, store, _, _ = memory
    job = store.jobs[job_id]
    store.jobs[job_id] = replace(job, status=JobStatus.COMPLETED)
    audio_path = tmp_path / "jobs" / job_id / "output.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"FAKEAUDIO")

    status = jobs_client.get(f"/api/jobs/{job_id}")
    assert status.json()["audio_url"] == f"/api/jobs/{job_id}/audio"

    response = jobs_client.get(f"/api/jobs/{job_id}/audio")
    assert response.status_code == 200
    assert response.content == b"FAKEAUDIO"
    assert response.headers["content-type"].startswith("audio/mpeg")
    assert response.headers["content-disposition"].startswith("inline")


def test_download_queued_is_409(jobs_client: TestClient) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    response = jobs_client.get(f"/api/jobs/{created['job_id']}/audio")
    assert response.status_code == 409
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_download_unknown_job_is_404(jobs_client: TestClient) -> None:
    job_id = str(uuid.uuid4())
    response = jobs_client.get(f"/api/jobs/{job_id}/audio")
    assert response.status_code == 404
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def _persist_status(tmp_path: Path, job: Job) -> None:
    asyncio.run(FilesystemJobStorage(tmp_path).save_job(job))


def test_retry_failed_returns_202_same_id(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    _, store, _, queue = memory
    job = store.jobs[job_id]
    failed = replace(
        job,
        status=JobStatus.FAILED,
        error_type=ErrorType.TTS_FAILED,
        message="tts failed",
    )
    store.jobs[job_id] = failed
    _persist_status(tmp_path, failed)
    queue.job_ids.clear()
    checkpoint = tmp_path / "jobs" / job_id / "checkpoint.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    seed = (
        '[{"artifact_path":"chunks/chunk-001.translated.txt",'
        '"chunk_id":"chunk-001","stage":"translated"}]'
    )
    checkpoint.write_text(seed, encoding="utf-8")

    response = jobs_client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "queued"
    retried = store.jobs[job_id]
    assert retried.status is JobStatus.QUEUED
    assert retried.error_type is None
    assert retried.message is None
    assert queue.job_ids == [job_id]
    assert checkpoint.read_text(encoding="utf-8") == seed


def test_retry_completed_is_409(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    _, store, _, queue = memory
    completed = replace(store.jobs[job_id], status=JobStatus.COMPLETED)
    store.jobs[job_id] = completed
    _persist_status(tmp_path, completed)
    queue.job_ids.clear()
    response = jobs_client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 409
    assert response.json() == {
        "error_type": ErrorType.INVALID_INPUT,
        "message": "job cannot be retried",
    }
    assert queue.job_ids == []


def test_retry_queued_is_409(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    _, store, _, _ = memory
    _persist_status(tmp_path, store.jobs[created["job_id"]])
    response = jobs_client.post(f"/api/jobs/{created['job_id']}/retry")
    assert response.status_code == 409
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_retry_uses_filesystem_status_not_store_cache(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    _, store, _, queue = memory
    job = store.jobs[job_id]
    store.jobs[job_id] = replace(job, status=JobStatus.COMPLETED)
    _persist_status(
        tmp_path,
        replace(
            job,
            status=JobStatus.FAILED,
            error_type=ErrorType.TTS_FAILED,
            message="tts failed",
        ),
    )
    queue.job_ids.clear()

    response = jobs_client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 202
    assert response.json() == {"job_id": job_id, "status": "queued"}
    assert store.jobs[job_id].status is JobStatus.QUEUED
    assert queue.job_ids == [job_id]


def test_retry_unknown_is_404(jobs_client: TestClient) -> None:
    job_id = str(uuid.uuid4())
    response = jobs_client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 404
    assert response.json() == {
        "error_type": ErrorType.INVALID_INPUT,
        "message": "job not found",
    }


def test_retry_rejects_non_uuid(jobs_client: TestClient) -> None:
    response = jobs_client.post("/api/jobs/not-a-uuid/retry")
    assert response.status_code == 400
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_get_job_service_caches_instance_on_app_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    service, _, _, _ = memory
    calls = 0

    def _fake_build(settings: Settings) -> JobService:
        nonlocal calls
        del settings
        calls += 1
        return service

    monkeypatch.setattr("app.api.deps.build_job_service", _fake_build)
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    with TestClient(app) as client:
        created = client.post("/api/jobs", json=VALID_BODY).json()
        client.get(f"/api/jobs/{created['job_id']}")
        client.get(f"/api/jobs/{created['job_id']}")
    assert calls == 1
    assert app.state.job_service is service


def test_post_srt_upload_is_accepted(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    srt_bytes = (
        b"1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n"
        b"2\n00:00:04,000 --> 00:00:06,000\nSecond line.\n"
    )
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "target_language": "ja-JP"},
        files={"file": ("captions.srt", srt_bytes, "application/x-subrip")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    _, _, source, _ = memory
    assert "Hello world." in source.texts[job_id]


def test_post_vtt_upload_is_accepted(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    vtt_bytes = (
        b"WEBVTT\n\n"
        b"00:00:01.000 --> 00:00:03.000\nHello WebVTT.\n\n"
        b"00:00:04.000 --> 00:00:06.000\nSecond cue.\n"
    )
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "target_language": "ja-JP"},
        files={"file": ("captions.vtt", vtt_bytes, "text/vtt")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    _, _, source, _ = memory
    assert "Hello WebVTT." in source.texts[job_id]
