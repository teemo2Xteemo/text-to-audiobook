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
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.main import create_app
from tests.fakes import InMemoryJobStore, InMemoryQueue, InMemorySourceStorage

VALID_BODY = {
    "text": "Once upon a time",
    "source_language": "en-US",
    "target_language": "ja-JP",
}

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,000
Once upon a time

"""


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


def test_get_job_subtitles(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    _, store, _, _ = memory
    job = store.jobs[job_id]
    store.jobs[job_id] = replace(job, status=JobStatus.COMPLETED)
    srt_path = tmp_path / "jobs" / job_id / "output.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(SAMPLE_SRT, encoding="utf-8")

    response = jobs_client.get(f"/api/jobs/{job_id}/subtitles")
    assert response.status_code == 200
    content_type = response.headers["content-type"]
    assert "text/plain" in content_type or "subrip" in content_type
    assert "00:00:01,000 -->" in response.text
    assert f"{job_id}.srt" in response.headers.get("content-disposition", "")


def test_get_job_subtitles_missing_srt_returns_404(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    _, store, _, _ = memory
    job = store.jobs[job_id]
    store.jobs[job_id] = replace(job, status=JobStatus.COMPLETED)

    response = jobs_client.get(f"/api/jobs/{job_id}/subtitles")
    assert response.status_code == 404
    assert response.json()["error_type"] == ErrorType.STORAGE_FAILED


def test_get_job_subtitles_queued_returns_409(jobs_client: TestClient) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    response = jobs_client.get(f"/api/jobs/{created['job_id']}/subtitles")
    assert response.status_code == 409
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_get_job_subtitles_unknown_job_returns_404(jobs_client: TestClient) -> None:
    job_id = str(uuid.uuid4())
    response = jobs_client.get(f"/api/jobs/{job_id}/subtitles")
    assert response.status_code == 404
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_get_job_subtitles_invalid_uuid_returns_400(jobs_client: TestClient) -> None:
    response = jobs_client.get("/api/jobs/not-a-uuid/subtitles")
    assert response.status_code == 400
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_job_status_includes_subtitles_url_when_available(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    _, store, _, _ = memory

    status_queued = jobs_client.get(f"/api/jobs/{job_id}").json()
    assert status_queued["subtitles_url"] is None

    job = store.jobs[job_id]
    store.jobs[job_id] = replace(job, status=JobStatus.COMPLETED)
    status_no_srt = jobs_client.get(f"/api/jobs/{job_id}").json()
    assert status_no_srt["subtitles_url"] is None

    srt_path = tmp_path / "jobs" / job_id / "output.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(SAMPLE_SRT, encoding="utf-8")
    status_with_srt = jobs_client.get(f"/api/jobs/{job_id}").json()
    assert status_with_srt["subtitles_url"] == f"/api/jobs/{job_id}/subtitles"
