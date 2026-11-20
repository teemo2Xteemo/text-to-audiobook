import asyncio
import json
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
from app.infrastructure.chapter_manifest_fs import FilesystemChapterManifest
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
            TranslationProfile(
                id="groq-qwen",
                provider="groq",
                model="qwen/qwen3.8-27b",
                label="Groq Qwen",
                badge="ONLINE",
                description="Specialized Asian stories",
                supported_languages=("en-US", "ja-JP", "vi-VN", "zh-CN", "ar-SA"),
            ),
        ],
        chapter_manifest_factory=FilesystemChapterManifest,
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


def test_post_with_groq_profile_resolves_profile(
    jobs_client: TestClient,
) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={
            "text": "林动深吸了一口气",
            "source_language": "auto",
            "target_language": "ar-SA",
            "translation_profile_id": "groq-qwen",
        },
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    status = jobs_client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["translation_provider"] == "groq"
    assert status.json()["translation_model"] == "qwen/qwen3.8-27b"


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
    assert payload["run_count"] == 0
    counted = JobStatusResponse.from_job(replace(job, run_count=4)).model_dump()
    assert counted["run_count"] == 4


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


def test_post_rejects_null_bytes(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={
            "text": "Hello\x00World",
            "source_language": "en-US",
            "target_language": "ja-JP",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error_type"] == ErrorType.INVALID_INPUT
    assert "null byte" in body["message"]


def _assert_payload_too_large(response, received: int, limit: int = 200) -> None:
    assert response.status_code == 413
    body = response.json()
    assert body["error_type"] == ErrorType.PAYLOAD_TOO_LARGE
    assert f"received {received} bytes" in body["message"]
    assert f"limit {limit} bytes" in body["message"]


def test_post_accepts_text_at_upload_limit(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={**VALID_BODY, "text": "x" * 200},
    )
    assert response.status_code == 202


def test_post_rejects_oversize_text(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={
            "text": "x" * 201,
            "source_language": "en-US",
            "target_language": "ja-JP",
        },
    )
    _assert_payload_too_large(response, 201)


def test_post_rejects_oversize_json_body_without_parsing(jobs_client: TestClient) -> None:
    payload = json.dumps({**VALID_BODY, "text": "x" * 5000}).encode()
    assert len(payload) > 200 + 4096
    response = jobs_client.post(
        "/api/jobs",
        content=payload,
        headers={"content-type": "application/json"},
    )
    _assert_payload_too_large(response, len(payload))


def test_post_accepts_file_at_upload_limit(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "target_language": "ja-JP"},
        files={"file": ("story.txt", b"x" * 200, "text/plain")},
    )
    assert response.status_code == 202


def test_post_rejects_oversize_file(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "target_language": "ja-JP"},
        files={"file": ("story.txt", b"x" * 201, "text/plain")},
    )
    _assert_payload_too_large(response, 201)


def test_post_rejects_chunked_body_without_content_length(jobs_client: TestClient) -> None:
    payload = json.dumps({**VALID_BODY, "text": "x" * 5000}).encode()
    assert len(payload) > 200 + 4096

    def chunks():
        midpoint = len(payload) // 2
        yield payload[:midpoint]
        yield payload[midpoint:]

    response = jobs_client.post(
        "/api/jobs",
        content=chunks(),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    body = response.json()
    assert body["error_type"] == ErrorType.PAYLOAD_TOO_LARGE
    # The remainder of a chunked body is not read, so the size is a floor.
    assert "received more than" in body["message"]
    assert "limit 200 bytes" in body["message"]


def test_post_rejects_chunked_file_without_content_length(jobs_client: TestClient) -> None:
    boundary = "----uploadlimit"
    file_bytes = b"x" * 201
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="source_language"\r\n\r\n'
            "en-US\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="target_language"\r\n\r\n'
            "ja-JP\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="story.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + file_bytes
        + f"\r\n--{boundary}--\r\n".encode()
    )

    def chunks():
        midpoint = len(body) // 2
        yield body[:midpoint]
        yield body[midpoint:]

    response = jobs_client.post(
        "/api/jobs",
        content=chunks(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    _assert_payload_too_large(response, len(file_bytes))


def test_multipart_text_is_not_capped_at_starlette_one_mebibyte(
    tmp_path: Path,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    # Starlette's default in-memory field cap is 1 MiB. A text part one byte
    # over that default must still be measured against MAX_UPLOAD_BYTES.
    limit = 1024 * 1024
    service, _, _, _ = memory
    settings = Settings(_env_file=None, storage_path=tmp_path, max_upload_bytes=limit)
    app = create_app(settings)
    app.state.job_service = service
    with TestClient(app) as client:
        # An empty file part forces multipart. httpx sends `data` as urlencoded
        # when no files are attached, which is not this endpoint's text path.
        response = client.post(
            "/api/jobs",
            data={
                "text": "x" * (limit + 1),
                "source_language": "en-US",
                "target_language": "ja-JP",
            },
            files={"_unused": ("", b"")},
        )
    _assert_payload_too_large(response, limit + 1, limit)


def test_detect_and_analyze_reject_oversize_json_text(jobs_client: TestClient) -> None:
    over = {"text": "x" * 201}
    _assert_payload_too_large(jobs_client.post("/api/detect-language", json=over), 201)
    _assert_payload_too_large(jobs_client.post("/api/analyze", json=over), 201)


def test_post_measures_cjk_text_in_bytes_not_characters(jobs_client: TestClient) -> None:
    # 66 CJK characters are 198 UTF-8 bytes, under the 200-byte cap.
    under = "中" * 66
    assert len(under) < 200
    assert len(under.encode()) == 198
    accepted = jobs_client.post("/api/jobs", json={**VALID_BODY, "text": under})
    assert accepted.status_code == 202

    # 67 characters are still under a 200-character reading, but 201 bytes.
    over = "中" * 67
    assert len(over) < 200
    assert len(over.encode()) == 201
    rejected = jobs_client.post("/api/jobs", json={**VALID_BODY, "text": over})
    _assert_payload_too_large(rejected, 201)


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


def test_get_language_detector_caches_instance_on_app_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.language_detection.cpu import CpuLanguageDetector

    calls = 0
    fake_detector = CpuLanguageDetector()

    def _fake_build(settings: Settings) -> CpuLanguageDetector:
        nonlocal calls
        calls += 1
        return fake_detector

    monkeypatch.setattr("app.api.deps.build_language_detector", _fake_build)
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    with TestClient(app) as client:
        client.post("/api/detect-language", json={"text": "hello world"})
        client.post("/api/detect-language", json={"text": "another world"})
    assert calls == 1
    assert app.state.language_detector is fake_detector


def test_get_tts_provider_caches_instance_on_app_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.tts.fake import FakeTTSProvider

    calls = 0
    fake_tts = FakeTTSProvider()

    def _fake_build(settings: Settings) -> FakeTTSProvider:
        nonlocal calls
        calls += 1
        return fake_tts

    monkeypatch.setattr("app.api.deps.build_tts_provider", _fake_build)
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    with TestClient(app) as client:
        client.post("/api/tts/preview", json={"text": "hello", "voice": "en-US-JennyNeural"})
        client.post("/api/tts/preview", json={"text": "world", "voice": "en-US-JennyNeural"})
    assert calls == 1
    assert app.state.tts_provider is fake_tts


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


def test_decode_source_text_encodings():
    from app.api.parse import _decode_source_text

    # UTF-8 with BOM
    text_utf8, enc = _decode_source_text(b"\xef\xbb\xbfHello world")
    assert text_utf8 == "Hello world"
    assert "utf-8" in enc.lower()

    # GBK Chinese
    sample_gbk = "第一章 序幕".encode("gbk")
    text_gbk, enc_gbk = _decode_source_text(sample_gbk)
    assert text_gbk == "第一章 序幕"
    assert "gb" in enc_gbk.lower()

    # Big5 Traditional Chinese
    sample_big5 = "第一章 序幕".encode("big5")
    text_big5, enc_big5 = _decode_source_text(sample_big5)
    assert text_big5 == "第一章 序幕"
    assert "big5" in enc_big5.lower()

    # UTF-16 LE with BOM
    sample_utf16_le = b"\xff\xfe" + "Hello world".encode("utf-16le")
    text_utf16_le, enc_utf16_le = _decode_source_text(sample_utf16_le)
    assert text_utf16_le == "Hello world"
    assert "utf-16" in enc_utf16_le.lower()

    # UTF-16 BE with BOM
    sample_utf16_be = b"\xfe\xff" + "Hello world".encode("utf-16be")
    text_utf16_be, enc_utf16_be = _decode_source_text(sample_utf16_be)
    assert text_utf16_be == "Hello world"
    assert "utf-16" in enc_utf16_be.lower()

    # Korean EUC-KR with Hanja (mixed script)
    sample_kr_hanja = "大韓民國의 憲法은 國民의 自由와 權利를 保障한다.".encode("euc-kr")
    text_kr_hanja, enc_kr_hanja = _decode_source_text(sample_kr_hanja)
    assert text_kr_hanja == "大韓民國의 憲法은 國民의 自由와 權利를 保障한다."
    assert any(k in enc_kr_hanja.lower() for k in ("kr", "949"))

    # Newline normalization
    text_newlines, _ = _decode_source_text(b"Line 1\r\nLine 2\rLine 3\n")
    assert text_newlines == "Line 1\nLine 2\nLine 3\n"


def test_decode_source_text_error_handling():
    from app.api.parse import _decode_source_text
    from app.domain.errors import DomainError

    # Unsupported / undecodable binary bytes
    with pytest.raises(DomainError) as exc_info:
        _decode_source_text(bytes([0x80, 0x81, 0x82, 0x83, 0x84, 0x85]))
    assert exc_info.value.error_type == ErrorType.INVALID_INPUT
    assert "unsupported encoding" in exc_info.value.message

    # Null bytes in decoded text
    with pytest.raises(DomainError) as exc_info_null:
        _decode_source_text(b"Hello\x00world")
    assert exc_info_null.value.error_type == ErrorType.INVALID_INPUT
    assert "null byte" in exc_info_null.value.message

    # Size limit is bytes, reported with both sizes.
    with pytest.raises(DomainError) as exc_info_limit:
        _decode_source_text(b"Hello world", max_upload_bytes=5)
    assert exc_info_limit.value.error_type == ErrorType.PAYLOAD_TOO_LARGE
    assert "received 11 bytes" in exc_info_limit.value.message
    assert "limit 5 bytes" in exc_info_limit.value.message


def test_post_txt_upload_gbk(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
) -> None:
    sample_gbk = "第一章 序幕\n天地不仁以万物为刍狗".encode("gbk")
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "zh-CN", "target_language": "en-US"},
        files={"file": ("story.txt", sample_gbk, "text/plain")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    _, _, source, _ = memory
    assert "第一章 序幕" in source.texts[job_id]


def test_post_narrate_job_json_and_get_status(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        json={
            "text": "Once upon a time in a faraway kingdom",
            "source_language": "en-US",
            "mode": "narrate",
        },
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    get_res = jobs_client.get(f"/api/jobs/{job_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["mode"] == "narrate"
    assert body["source_language"] == "en-US"
    assert body["target_language"] == "en-US"
    assert "translation_provider" not in body


def test_post_narrate_job_multipart(jobs_client: TestClient) -> None:
    response = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "mode": "narrate"},
        files={"file": ("story.txt", b"Once upon a time in a kingdom", "text/plain")},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    get_res = jobs_client.get(f"/api/jobs/{job_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["mode"] == "narrate"
    assert body["source_language"] == "en-US"
    assert body["target_language"] == "en-US"


def test_post_json_translate_missing_languages_raises_invalid_input(
    jobs_client: TestClient,
) -> None:
    # Missing source_language
    res_src = jobs_client.post(
        "/api/jobs",
        json={"text": "Story", "target_language": "ja-JP"},
    )
    assert res_src.status_code == 400
    assert res_src.json()["error_type"] == ErrorType.INVALID_INPUT
    assert "source_language is required" in res_src.json()["message"]

    # Missing target_language
    res_tgt = jobs_client.post(
        "/api/jobs",
        json={"text": "Story", "source_language": "en-US"},
    )
    assert res_tgt.status_code == 400
    assert res_tgt.json()["error_type"] == ErrorType.INVALID_INPUT
    assert "target_language is required" in res_tgt.json()["message"]


def test_detect_language_endpoint(client: TestClient) -> None:
    res = client.post(
        "/api/detect-language", json={"text": "Xin chào thế giới, đây là tiếng Việt."}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["language"] == "vi-VN"
    assert "candidates" in data
    assert "needs_confirmation" in data
    assert "preview_lines" in data


def test_analyze_endpoint(client: TestClient) -> None:
    sample = "第一章 开篇\n这是中文测试。\n第二章 发展\n继续测试。"
    res = client.post("/api/analyze", json={"text": sample})
    assert res.status_code == 200
    data = res.json()
    assert data["encoding"] == "utf-8"
    assert data["chapters_count"] == 2
    assert data["chapters_span_count"] == 2
    assert "confidence" not in data["chapters_preview"][0]
    assert "duration_estimate_seconds" in data
    assert "process_time_estimate_seconds" in data
    assert "preview_lines" in data
    assert data["chapter_detection_uncertain"] is False
    assert data["chapter_detection_confidence"] >= 0.8
    assert len(data["chapters_preview"]) == 2
    assert data["chapters_preview"][0]["kind"] == "chapter"
    assert data["chapters_preview"][0]["ordinal"] == 1


def test_tts_preview_endpoint(client: TestClient) -> None:
    res = client.post(
        "/api/tts/preview", json={"text": "Hello world", "voice": "en-US-JennyNeural"}
    )
    assert res.status_code == 200
    data = res.json()
    assert "audio_base64" in data
    assert data["mime_type"] == "audio/mpeg"


def test_detect_language_empty_text(client: TestClient) -> None:
    res = client.post("/api/detect-language", json={"text": "   "})
    assert res.status_code == 400
    assert res.json()["error_type"] == ErrorType.INVALID_INPUT
    assert "text is required" in res.json()["message"]


def test_detect_language_raw_text(client: TestClient) -> None:
    res = client.post(
        "/api/detect-language",
        content="Xin chào thế giới, đây là tiếng Việt.".encode(),
        headers={"content-type": "text/plain"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["language"] == "vi-VN"


def test_analyze_multipart_file(client: TestClient) -> None:
    content = "第一章 开篇\n测试内容。\n第二章 结局\n完结。"
    res = client.post(
        "/api/analyze",
        files={"file": ("story.txt", content.encode("utf-8"), "text/plain")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["encoding"] == "utf-8"
    assert data["chapters_count"] == 2
    assert len(data["chapters_preview"]) == 2
    assert data["chapters_preview"][0]["title"] == "第一章 开篇"


def test_analyze_preface_and_volume_does_not_inflate_chapters_count(client: TestClient) -> None:
    # R4: Preface and volume headings should not inflate the chapters_count
    content = "序言\n前言内容。\n第一卷 少年\n第一章 开端\n正文一。\n第二章 发展\n正文二。"
    res = client.post("/api/analyze", json={"text": content})
    assert res.status_code == 200
    data = res.json()
    assert data["chapters_count"] == 2
    # Preface and volume stay in the preview but do not share index 0, and
    # they are not counted as chapters.
    assert data["chapter_detection_uncertain"] is False
    assert data["chapters_preview_truncated"] is False
    preview = data["chapters_preview"]
    assert [item["kind"] for item in preview] == ["preface", "volume", "chapter", "chapter"]
    assert [item["index"] for item in preview] == [1, 2, 3, 4]
    assert [item["ordinal"] for item in preview] == [1, 2, 3, 4]
    assert preview[2]["title"] == "第一章 开端"
    assert preview[2]["chapter_number"] == 1
    assert preview[3]["chapter_number"] == 2
    assert preview[0]["chapter_number"] is None
    assert preview[1]["chapter_number"] is None


def test_analyze_empty_text(client: TestClient) -> None:
    res = client.post("/api/analyze", json={"text": ""})
    assert res.status_code == 400
    assert res.json()["error_type"] == ErrorType.INVALID_INPUT
    assert "text is required" in res.json()["message"]


def test_tts_preview_truncates_long_text(client: TestClient) -> None:
    long_text = "Hello world! This is a long story segment. " + ("word " * 60)
    res = client.post("/api/tts/preview", json={"text": long_text, "voice": "en-US-JennyNeural"})
    assert res.status_code == 200
    assert "audio_base64" in res.json()


def test_tts_preview_truncation_logic() -> None:
    from app.api.jobs import truncate_preview_text

    assert truncate_preview_text("Short text.") == "Short text."
    prefix = "Hello world! "
    text = prefix + ("x" * 350)
    assert truncate_preview_text(text) == "Hello world!"

    cjk_prefix = "第一句话。第二句话！"
    cjk_text = cjk_prefix + ("字" * 350)
    assert truncate_preview_text(cjk_text) == "第一句话。第二句话！"

    no_punct = "a" * 350
    assert truncate_preview_text(no_punct) == "a" * 300


def test_tts_preview_rate_limit(tmp_path: Path) -> None:
    from app.api.jobs import _preview_rate_limiter

    _preview_rate_limiter.reset()
    try:
        settings = Settings(_env_file=None, storage_path=tmp_path, tts_preview_rate_per_minute=2)
        app = create_app(settings)
        with TestClient(app) as test_client:
            res1 = test_client.post(
                "/api/tts/preview", json={"text": "hello", "voice": "en-US-JennyNeural"}
            )
            assert res1.status_code == 200
            res2 = test_client.post(
                "/api/tts/preview", json={"text": "world", "voice": "en-US-JennyNeural"}
            )
            assert res2.status_code == 200
            res3 = test_client.post(
                "/api/tts/preview", json={"text": "again", "voice": "en-US-JennyNeural"}
            )
            assert res3.status_code == 429
            assert res3.json()["error_type"] == ErrorType.PROVIDER_RATE_LIMIT
            assert "Too many preview requests" in res3.json()["message"]
    finally:
        _preview_rate_limiter.reset()


def test_tts_preview_spoofed_x_forwarded_for_shares_bucket(tmp_path: Path) -> None:
    from app.api.jobs import _preview_rate_limiter

    _preview_rate_limiter.reset()
    try:
        settings = Settings(_env_file=None, storage_path=tmp_path, tts_preview_rate_per_minute=2)
        app = create_app(settings)
        with TestClient(app) as test_client:
            # Client rotates X-Forwarded-For to try to evade limit
            res1 = test_client.post(
                "/api/tts/preview",
                json={"text": "hello", "voice": "en-US-JennyNeural"},
                headers={"X-Forwarded-For": "1.1.1.1"},
            )
            assert res1.status_code == 200
            res2 = test_client.post(
                "/api/tts/preview",
                json={"text": "world", "voice": "en-US-JennyNeural"},
                headers={"X-Forwarded-For": "2.2.2.2"},
            )
            assert res2.status_code == 200
            res3 = test_client.post(
                "/api/tts/preview",
                json={"text": "again", "voice": "en-US-JennyNeural"},
                headers={"X-Forwarded-For": "3.3.3.3"},
            )
            assert res3.status_code == 429
            assert res3.json()["error_type"] == ErrorType.PROVIDER_RATE_LIMIT
    finally:
        _preview_rate_limiter.reset()


def test_tts_preview_timeout_error(tmp_path: Path) -> None:
    import asyncio

    from app.api.deps import get_tts_provider
    from app.api.jobs import _preview_rate_limiter
    from app.domain.audio import AudioArtifact, TTSSettings

    _preview_rate_limiter.reset()

    class _HangingTTS:
        async def synthesize(
            self, text: str, language: str, voice: str, settings: TTSSettings
        ) -> AudioArtifact:
            await asyncio.sleep(1.0)
            return AudioArtifact(path=tmp_path / "never.mp3")

        def voices_for(self, language: str) -> list:
            return []

        def languages(self) -> set[str]:
            return set()

        def all_voices(self) -> list:
            return []

    try:
        settings = Settings(
            _env_file=None,
            storage_path=tmp_path,
            tts_preview_timeout_seconds=0.05,
        )
        app = create_app(settings)
        app.dependency_overrides[get_tts_provider] = lambda: _HangingTTS()
        with TestClient(app) as test_client:
            res = test_client.post(
                "/api/tts/preview",
                json={"text": "hello hanging preview", "voice": "en-US-JennyNeural"},
            )
            assert res.status_code == 504
            assert res.json()["error_type"] == ErrorType.TIMEOUT
    finally:
        _preview_rate_limiter.reset()


def test_post_narrate_accepts_srt_phase_2(jobs_client: TestClient) -> None:
    srt_text = (
        "1\n"
        "00:00:01,000 --> 00:00:04,000\n"
        "Hello subtitle world.\n\n"
        "2\n"
        "00:00:05,000 --> 00:00:08,000\n"
        "Second subtitle line.\n"
    )
    res = jobs_client.post(
        "/api/jobs",
        json={
            "text": srt_text,
            "mode": "narrate",
            "source_language": "en-US",
            "voice": "fake-en-US-a",
        },
    )
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    get_res = jobs_client.get(f"/api/jobs/{job_id}")
    assert get_res.status_code == 200
    body = get_res.json()
    assert body["mode"] == "narrate"


_SRT_SAMPLE = "1\n00:00:01,000 --> 00:00:04,000\nHello subtitle world.\n"


def test_post_narrate_srt_timing_json_and_multipart(jobs_client: TestClient) -> None:
    res = jobs_client.post(
        "/api/jobs",
        json={
            "text": _SRT_SAMPLE,
            "mode": "narrate",
            "source_language": "en-US",
            "srt_timing": "aligned",
        },
    )
    assert res.status_code == 202
    body = jobs_client.get(f"/api/jobs/{res.json()['job_id']}").json()
    assert body["srt_timing"] == "aligned"

    res = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "mode": "narrate", "srt_timing": "aligned"},
        files={"file": ("captions.srt", _SRT_SAMPLE.encode(), "application/x-subrip")},
    )
    assert res.status_code == 202
    body = jobs_client.get(f"/api/jobs/{res.json()['job_id']}").json()
    assert body["srt_timing"] == "aligned"


def test_post_job_rejects_invalid_or_translate_srt_timing(jobs_client: TestClient) -> None:
    res = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "mode": "narrate", "srt_timing": "sideways"},
        files={"file": ("captions.srt", _SRT_SAMPLE.encode(), "application/x-subrip")},
    )
    assert res.status_code == 400
    assert res.json()["error_type"] == "INVALID_INPUT"

    res = jobs_client.post(
        "/api/jobs",
        json={
            "text": _SRT_SAMPLE,
            "source_language": "en-US",
            "target_language": "ko-KR",
            "srt_timing": "aligned",
        },
    )
    assert res.status_code == 400
    assert "narrate mode" in res.json()["message"]


def test_post_job_rejects_non_boolean_normalize_text(jobs_client: TestClient) -> None:
    res = jobs_client.post(
        "/api/jobs",
        data={"source_language": "en-US", "mode": "narrate", "normalize_text": "maybe"},
        files={"file": ("story.txt", b"Chapter 20 begins.", "text/plain")},
    )
    assert res.status_code == 400
    assert res.json()["message"] == "normalize_text must be true or false"


def test_decode_source_text_small_gbk_and_big5_not_mojibake() -> None:
    from app.api.parse import _decode_source_text

    gbk_sample = "天王盖地虎，宝塔镇河妖。这是几百字节的中文小说片段。".encode("gbk")
    text, enc = _decode_source_text(gbk_sample)
    assert "天王盖地虎" in text
    assert enc.lower() in ("gb18030", "gbk", "gb2312")

    big5_sample = "倚天屠龍記，金庸武俠小說經典。繁體中文編碼測試。".encode("big5")
    text_b5, enc_b5 = _decode_source_text(big5_sample)
    assert "倚天屠龍記" in text_b5
    assert enc_b5.lower() in ("big5", "big5hkscs", "cp950")


def test_detect_language_rejects_null_bytes(client: TestClient) -> None:
    res = client.post(
        "/api/detect-language",
        content=b"hello \x00 null bytes",
        headers={"content-type": "text/plain"},
    )
    assert res.status_code == 400
    assert res.json()["error_type"] == ErrorType.INVALID_INPUT


def test_detect_language_multipart_text(client: TestClient) -> None:
    res = client.post(
        "/api/detect-language",
        data={"text": "Hello world, this is an English sentence for language detection."},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["language"] == "en-US"
    assert "confidence" in data


def test_detect_language_multipart_file(client: TestClient) -> None:
    content = b"Konnichiwa, watashi wa nihongo desu."
    res = client.post(
        "/api/detect-language",
        files={"file": ("story.txt", content, "text/plain")},
    )
    assert res.status_code == 200
    data = res.json()
    assert "language" in data
    assert "confidence" in data


def test_tts_preview_empty_fields(client: TestClient) -> None:
    res1 = client.post("/api/tts/preview", json={"text": "", "voice": "en-US-JennyNeural"})
    assert res1.status_code == 400
    assert "text is required" in res1.json()["message"]

    res2 = client.post("/api/tts/preview", json={"text": "hi", "voice": "  "})
    assert res2.status_code == 400
    assert "voice is required" in res2.json()["message"]


def test_tts_preview_invalid_speed(client: TestClient) -> None:
    res = client.post(
        "/api/tts/preview", json={"text": "hi", "voice": "en-US-JennyNeural", "speed": 3.0}
    )
    assert res.status_code == 400
    assert res.json()["error_type"] == ErrorType.INVALID_INPUT
    assert "speed must be between" in res.json()["message"]


def test_json_and_multipart_carry_the_same_tts_engine(tmp_path: Path) -> None:
    from app.providers.tts.fake import FakeTTSProvider

    service = JobService(
        jobs=InMemoryJobStore(),
        source_storage=InMemorySourceStorage(),
        queue=InMemoryQueue(),
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=FilesystemJobStorage(tmp_path),
        tts_engines={"fish": FakeTTSProvider()},
    )
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    app.state.job_service = service
    with TestClient(app) as client:
        as_json = client.post("/api/jobs", json={**VALID_BODY, "tts_engine": "fish"})
        as_form = client.post(
            "/api/jobs",
            data={"source_language": "en-US", "target_language": "ja-JP", "tts_engine": "fish"},
            files={"file": ("story.txt", b"Once upon a time", "text/plain")},
        )
        bad = client.post("/api/jobs", json={**VALID_BODY, "tts_engine": "nope"})
        for response in (as_json, as_form):
            assert response.status_code == 202
            status = client.get(f"/api/jobs/{response.json()['job_id']}")
            assert status.json()["tts_engine"] == "fish"
        assert bad.status_code == 400
        assert bad.json()["error_type"] == "INVALID_INPUT"


def test_translate_cross_engine_voice_is_400_at_create(tmp_path: Path) -> None:
    from app.domain.audio import Voice
    from app.providers.tts.fake import FakeTTSProvider

    edge = FakeTTSProvider(voices=[Voice(id="edge-ja", language="ja-JP", label="e")])
    service = JobService(
        jobs=InMemoryJobStore(),
        source_storage=InMemorySourceStorage(),
        queue=InMemoryQueue(),
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=FilesystemJobStorage(tmp_path),
        tts=edge,
        tts_engines={"fake": edge},
    )
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    app.state.job_service = service
    with TestClient(app) as client:
        response = client.post("/api/jobs", json={**VALID_BODY, "voice": "fish:abc"})
        assert response.status_code == 400
        assert response.json()["error_type"] == "UNSUPPORTED_LANGUAGE"


def _preview_app(tmp_path: Path, engines: dict[str, object]) -> TestClient:
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    app.state.tts_engines = engines
    return TestClient(app)


def test_preview_with_engine_uses_that_provider_and_cache(tmp_path: Path) -> None:
    from app.providers.tts.fake import FakeTTSProvider

    class _Counting(FakeTTSProvider):
        calls = 0

        async def synthesize(self, *a: object, **kw: object):  # type: ignore[no-untyped-def]
            _Counting.calls += 1
            return await super().synthesize(*a, **kw)  # type: ignore[arg-type]

    fish = _Counting(output_dir=tmp_path / "t")
    with _preview_app(tmp_path, {"fake": FakeTTSProvider(), "fish": fish}) as client:
        body = {"text": "hello", "voice": "fish:abc", "tts_engine": "fish"}
        first = client.post("/api/tts/preview", json=body)
        second = client.post("/api/tts/preview", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert _Counting.calls == 1


def test_preview_with_disabled_engine_is_invalid_input(tmp_path: Path) -> None:
    from app.providers.tts.fake import FakeTTSProvider

    with _preview_app(tmp_path, {"fake": FakeTTSProvider()}) as client:
        res = client.post(
            "/api/tts/preview", json={"text": "hi", "voice": "v", "tts_engine": "fish"}
        )
    assert res.status_code == 400
    assert res.json()["error_type"] == "INVALID_INPUT"


def test_post_job_with_fish_and_no_key_is_provider_configuration_error(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    from app.providers.tts.fake import FakeTTSProvider
    from app.providers.tts.fish import FishTTSProvider

    service = memory[0]
    service._tts_engines = {
        "fake": FakeTTSProvider(),
        "fish": FishTTSProvider(
            tmp_path,
            api_key=None,
            base_url="http://x",
            model="m",
            voices=[],
            timeout_seconds=1,
            max_concurrency=1,
        ),
    }
    res = jobs_client.post("/api/jobs", json={**VALID_BODY, "tts_engine": "fish"})
    assert res.status_code == 400
    assert res.json()["error_type"] == "PROVIDER_CONFIGURATION_ERROR"


def test_preview_with_vieneu_engine_returns_wav(tmp_path: Path) -> None:
    import json as _json

    from app.providers.tts.vieneu import VieneuTTSProvider

    class _Resp:
        def __init__(self, data: bytes) -> None:
            self.data = data

        def read(self) -> bytes:
            return self.data

        def __enter__(self) -> "_Resp":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def opener(request, timeout=None):  # type: ignore[no-untyped-def]
        if request.full_url.endswith("/voices"):
            return _Resp(_json.dumps({"voices": ["Bao"]}).encode())
        return _Resp(b"RIFFWAVE")

    vieneu = VieneuTTSProvider(
        tmp_path / "t", base_url="http://v/v1", model="m", timeout_seconds=5, opener=opener
    )
    body = {"text": "xin chao", "voice": "vieneu:Bao", "tts_engine": "vieneu"}
    with _preview_app(tmp_path, {"vieneu": vieneu}) as client:
        res = client.post("/api/tts/preview", json=body)
    assert res.status_code == 200
    assert res.json()["mime_type"] == "audio/wav"


def _save_manifest(tmp_path: Path, job_id: str, chapters: list) -> None:
    from app.domain.chapters import ChapterManifest

    FilesystemChapterManifest(tmp_path / "jobs" / job_id).save(
        ChapterManifest(
            version=1,
            max_chars=1200,
            chapter_aware=True,
            detector="v2",
            chapters=chapters,
        )
    )


def test_chapter_download_while_generating_and_partial_zip(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    import io
    import zipfile

    from app.domain.chapters import ChapterRecord, ChapterStatus

    _, store, _, _ = memory
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    job = asyncio.run(store.get(job_id))
    assert job is not None
    asyncio.run(
        store.save(replace(job, status=JobStatus.GENERATING_AUDIO, chunk_current=1, chunk_total=4))
    )
    directory = tmp_path / "jobs" / job_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "secret.mp3").write_bytes(b"secret")
    chapter = directory / "chapters" / "0001.mp3"
    chapter.parent.mkdir(parents=True)
    chapter.write_bytes(b"mp3!")
    _save_manifest(
        tmp_path,
        job_id,
        [
            ChapterRecord(
                ordinal=1,
                kind="chapter",
                chapter_number=1,
                title_source="第一章 少年",
                title="Chương 1 Thiếu niên",
                chunk_first=1,
                chunk_last=2,
                status=ChapterStatus.READY,
                file="../secret.mp3",
                duration_ms=462000,
                size_bytes=4,
            ),
            ChapterRecord(
                ordinal=2,
                kind="chapter",
                chapter_number=2,
                title_source="第二章",
                title="Chương 2",
                chunk_first=3,
                chunk_last=4,
                status=ChapterStatus.PENDING,
            ),
        ],
    )

    status = jobs_client.get(f"/api/jobs/{job_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["chapters_total"] == 2
    assert body["chapters_ready"] == 1
    assert body["chapters_failed"] == 0
    assert body["chapters_url"] == f"/api/jobs/{job_id}/chapters"
    assert body["zip_url"] == f"/api/jobs/{job_id}/chapters.zip"
    assert body["audio_url"] is None

    listed = jobs_client.get(f"/api/jobs/{job_id}/chapters")
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["partial"] is True
    assert payload["items"][0]["status"] == "ready"
    assert payload["items"][0]["duration_ms"] == 462000
    assert payload["items"][0]["filename"].startswith("0001 - ")
    assert payload["items"][0]["filename"].endswith(".mp3")
    assert "Thiếu niên" in payload["items"][0]["filename"]
    assert payload["items"][1]["download_url"] is None

    ready_only = jobs_client.get(f"/api/jobs/{job_id}/chapters", params={"status": "ready"})
    assert [item["ordinal"] for item in ready_only.json()["items"]] == [1]

    audio = jobs_client.get(f"/api/jobs/{job_id}/chapters/1/audio")
    assert audio.status_code == 200
    assert audio.content == b"mp3!"
    disposition = audio.headers["content-disposition"]
    assert "filename*=" in disposition
    assert "0001" in disposition

    attachment = jobs_client.get(f"/api/jobs/{job_id}/chapters/1/audio", params={"download": 1})
    assert "attachment" in attachment.headers["content-disposition"]

    pending = jobs_client.get(f"/api/jobs/{job_id}/chapters/2/audio")
    assert pending.status_code == 409
    assert pending.json()["message"] == "chapter not ready"

    missing = jobs_client.get(f"/api/jobs/{job_id}/chapters/9/audio")
    assert missing.status_code == 404

    bad = jobs_client.get(f"/api/jobs/{job_id}/chapters/not-a-number/audio")
    assert bad.status_code == 400

    zipped = jobs_client.get(f"/api/jobs/{job_id}/chapters.zip")
    assert zipped.status_code == 200
    assert zipped.headers["content-type"].startswith("application/zip")
    assert zipped.headers["x-chapters-partial"] == "true"
    assert zipped.headers["x-chapters-included"] == "1/2"
    with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
        assert archive.namelist() == [payload["items"][0]["filename"]]
        assert archive.read(archive.namelist()[0]) == b"mp3!"


def test_legacy_job_has_empty_chapter_list_and_keeps_audio(
    jobs_client: TestClient,
    memory: tuple[JobService, InMemoryJobStore, InMemorySourceStorage, InMemoryQueue],
    tmp_path: Path,
) -> None:
    _, store, _, _ = memory
    created = jobs_client.post("/api/jobs", json=VALID_BODY).json()
    job_id = created["job_id"]
    listed = jobs_client.get(f"/api/jobs/{job_id}/chapters")
    assert listed.status_code == 200
    assert listed.json()["total"] == 0
    assert listed.json()["items"] == []
    status = jobs_client.get(f"/api/jobs/{job_id}")
    assert status.json()["chapters_total"] == 0
    assert status.json()["chapters_url"] is None
    assert status.json()["zip_url"] is None
    assert jobs_client.get(f"/api/jobs/{job_id}/chapters.zip").status_code == 409

    job = asyncio.run(store.get(job_id))
    assert job is not None
    asyncio.run(
        store.save(replace(job, status=JobStatus.COMPLETED, chunk_total=1, chunk_current=1))
    )
    audio = tmp_path / "jobs" / job_id / "output.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"book")
    downloaded = jobs_client.get(f"/api/jobs/{job_id}/audio")
    assert downloaded.status_code == 200
    assert downloaded.content == b"book"
