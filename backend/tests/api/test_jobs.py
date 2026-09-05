import uuid
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.jobs import JobService
from app.config.settings import Settings
from app.domain.errors import ErrorType
from app.domain.jobs import JobStatus
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
