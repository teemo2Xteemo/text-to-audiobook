import uuid
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.jobs import JobService
from app.config.settings import Settings
from app.domain.errors import ErrorType
from app.domain.jobs import JobMode, JobStatus
from app.domain.subtitles import DocumentFormat
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.main import create_app
from tests.fakes import InMemoryJobStore, InMemoryQueue, InMemorySourceStorage

ASS_TEXT = (
    "[Script Info]\nScriptType: v4.00+\n\n[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Hello\n"
)
SRT_TEXT = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"


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
def client(
    tmp_path: Path,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> Generator[TestClient, None, None]:
    service, _, _, _ = memory
    settings = Settings(_env_file=None, storage_path=tmp_path, max_upload_bytes=20_000)
    app = create_app(settings)
    app.state.job_service = service
    with TestClient(app) as test_client:
        yield test_client


def test_upload_ass_creates_subtitle_job(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        data={
            "mode": "subtitle",
            "source_language": "en-US",
            "target_language": "vi-VN",
            "voice": "should-be-ignored",
            "speed": "9",
            "output_format": "wav",
            "tts_engine": "not-a-real-engine",
            "normalize_text": "true",
            "srt_timing": "aligned",
        },
        files={"file": ("ep01.ass", ASS_TEXT, "text/x-ssa")},
    )
    assert response.status_code == 202


def test_json_subtitle_ignores_invalid_tts_fields(client: TestClient) -> None:
    response = client.post(
        "/api/jobs",
        json={
            "text": "Hello there",
            "mode": "subtitle",
            "source_language": "en-US",
            "target_language": "vi-VN",
            "voice": "x",
            "speed": 99,
            "output_format": "nope",
            "tts_engine": "missing",
            "normalize_text": True,
            "srt_timing": "aligned",
            "document_format": "vtt",
        },
    )
    assert response.status_code == 202


def test_txt_containing_srt_locks_format(
    client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    response = client.post(
        "/api/jobs",
        data={
            "mode": "subtitle",
            "source_language": "en-US",
            "target_language": "vi-VN",
            "document_format": "txt",
        },
        files={"file": ("notes.txt", SRT_TEXT, "text/plain")},
    )
    assert response.status_code == 202
    _, store, _, _ = memory
    job = store.jobs[response.json()["job_id"]]
    assert job.document_format is DocumentFormat.SRT
    assert job.source_name == "notes"


def test_document_download_filename(
    client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    created = client.post(
        "/api/jobs",
        data={
            "mode": "subtitle",
            "source_language": "en-US",
            "target_language": "vi-VN",
        },
        files={"file": ("ep01.srt", SRT_TEXT, "application/x-subrip")},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    _, store, _, _ = memory
    job = store.jobs[job_id]
    store.jobs[job_id] = replace(job, status=JobStatus.COMPLETED, line_total=1)
    path = tmp_path / "jobs" / job_id / "output.srt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SRT_TEXT, encoding="utf-8")

    status = client.get(f"/api/jobs/{job_id}")
    body = status.json()
    assert body["document_url"].endswith("/document")
    assert body["audio_url"] is None
    assert body["document_format"] == "srt"
    assert body["line_total"] == 1

    downloaded = client.get(f"/api/jobs/{job_id}/document")
    assert downloaded.status_code == 200
    assert 'filename="ep01.vi-VN.srt"' in downloaded.headers["content-disposition"]
    assert "subrip" in downloaded.headers["content-type"]


def test_document_not_ready_is_409(client: TestClient) -> None:
    created = client.post(
        "/api/jobs",
        json={
            "text": "Hello",
            "mode": "subtitle",
            "source_language": "en-US",
            "target_language": "vi-VN",
        },
    )
    assert created.status_code == 202
    response = client.get(f"/api/jobs/{created.json()['job_id']}/document")
    assert response.status_code == 409
    assert response.json()["error_type"] == ErrorType.INVALID_INPUT


def test_document_unknown_and_bad_id(client: TestClient) -> None:
    missing = client.get(f"/api/jobs/{uuid.uuid4()}/document")
    assert missing.status_code == 404
    invalid = client.get("/api/jobs/not-a-uuid/document")
    assert invalid.status_code == 400


def test_chapters_for_subtitle_job_are_empty(
    client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    created = client.post(
        "/api/jobs",
        json={
            "text": "Chapter 1\n\nHello",
            "mode": "subtitle",
            "source_language": "en-US",
            "target_language": "vi-VN",
        },
    )
    job_id = created.json()["job_id"]
    _, store, _, _ = memory
    store.jobs[job_id] = replace(store.jobs[job_id], status=JobStatus.COMPLETED)
    listed = client.get(f"/api/jobs/{job_id}/chapters")
    assert listed.status_code == 200
    assert listed.json()["total"] == 0
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["chapters_total"] == 0
    assert status["chapters_url"] is None
    assert store.jobs[job_id].mode is JobMode.SUBTITLE
