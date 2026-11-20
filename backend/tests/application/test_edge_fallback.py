"""Continue a failed Fish/VieNeu job on Edge without rebuilding finished chapters."""

from __future__ import annotations

import asyncio
import fcntl
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.jobs import JobService
from app.application.pipeline.edge_fallback import (
    LATER_CHAPTERS_WARNING,
    SINGLE_SPAN_WARNING,
    is_tts_engine_failure,
)
from app.config.settings import Settings
from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobMode, JobStatus
from app.domain.retry import RetryPolicy
from app.infrastructure.chapter_manifest_fs import FilesystemChapterManifest
from app.infrastructure.checkpoint_fs import CheckpointFilesystem
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.main import create_app
from app.providers.tts.fake import FakeTTSProvider
from tests.application.test_pipeline import (
    _Clock,
    _ClockTTS,
    _job,
    _orchestrator,
    _patch_clock,
    _run,
    _voices_for,
)
from tests.fakes import (
    FakeTranslationProvider,
    InMemoryJobStore,
    InMemoryQueue,
    InMemorySourceStorage,
)

_STORY = (
    "Chapter 1: Dawn\n\n"
    "The sun rose over the quiet valley.\n\n"
    "Chapter 2: Dusk\n\n"
    "The sun set behind the hill.\n\n"
    "The night grew cold and quiet.\n\n"
    "Chapter 3: Morning\n\n"
    "Birds began to sing at the window.\n"
)
_PLAIN = (
    "The sun rose over the quiet valley and warmed the road. "
    "The night grew cold and quiet before anyone spoke again."
)
_JOB_ID = "11111111-1111-4111-8111-111111111111"
_NO_RETRY = RetryPolicy(max_attempts=1, backoff_seconds=0, rate_limit_max_attempts=1)


class _MarkedTTS(FakeTTSProvider):
    def __init__(
        self,
        mark: bytes,
        voices: list[Voice],
        output_dir: Path,
        *,
        fail_on: str | None = None,
    ) -> None:
        super().__init__(voices, output_dir)
        self.mark = mark
        self.fail_on = fail_on

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        if self.fail_on and self.fail_on in text:
            raise DomainError(ErrorType.PROVIDER_RATE_LIMIT, "tts rate limited")
        artifact = await super().synthesize(text, language, voice, settings)
        artifact.path.write_bytes(self.mark)
        return artifact


class _Store:
    def __init__(self, filesystem: FilesystemJobStorage) -> None:
        self._filesystem = filesystem

    async def save(self, job: Job) -> None:
        await self._filesystem.save_job(job)

    async def get(self, job_id: str) -> Job | None:
        return await self._filesystem.get_job(job_id)

    async def delete(self, job_id: str) -> None:
        await self._filesystem.delete_job(job_id)

    async def list_ids(self) -> list[str]:
        return list(await self._filesystem.list_job_ids())


def _service(
    tmp_path: Path,
    *,
    scope: str = "chapter",
    edge: FakeTTSProvider | None = None,
    queue: InMemoryQueue | None = None,
) -> tuple[JobService, InMemoryQueue, FilesystemJobStorage]:
    filesystem = FilesystemJobStorage(tmp_path)
    voices = edge or FakeTTSProvider(_voices_for("en-US", "ja-JP"), tmp_path / "edge-voices")
    service = JobService(
        jobs=_Store(filesystem),  # type: ignore[arg-type]
        source_storage=filesystem,
        queue=queue or InMemoryQueue(),
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=filesystem,
        tts_engines={"edge": voices, "fish": voices, "fake": voices},
        default_tts_engine="fake",
        fallback_engine="edge",
        fallback_scope=scope,
        chapter_manifest_factory=FilesystemChapterManifest,
        checkpoint_storage_factory=CheckpointFilesystem,
    )
    used = queue or service._queue
    assert isinstance(used, InMemoryQueue)
    return service, used, filesystem


def _failed(
    *,
    engine: str = "fish",
    error: ErrorType | None = ErrorType.PROVIDER_RATE_LIMIT,
    message: str | None = "tts rate limited",
    stage: str | None = "generating_audio",
    status: JobStatus = JobStatus.FAILED,
    mode: JobMode = JobMode.NARRATE,
) -> Job:
    return _job(
        id=_JOB_ID,
        status=status,
        source_language="en-US",
        target_language="en-US",
        voice="fish:story",
        mode=mode,
        tts_engine=engine,
        error_type=error,
        message=message,
        error_stage=stage,
    )


def test_engine_failure_classifier_keeps_translation_and_worker_timeouts() -> None:
    fish = _failed()
    assert is_tts_engine_failure(fish)
    assert is_tts_engine_failure(
        replace(fish, error_type=ErrorType.TIMEOUT, message="tts request timed out")
    )
    assert is_tts_engine_failure(
        replace(
            fish,
            error_type=ErrorType.PROVIDER_CONFIGURATION_ERROR,
            message="tts provider quota or plan limit reached (HTTP 402)",
        )
    )
    assert not is_tts_engine_failure(
        replace(
            fish,
            error_type=ErrorType.TIMEOUT,
            message="job exceeded the worker timeout (1800s)",
        )
    )
    assert not is_tts_engine_failure(
        replace(
            fish,
            mode=JobMode.TRANSLATE,
            error_type=ErrorType.PROVIDER_RATE_LIMIT,
            message="translation rate limited",
            error_stage="translating",
        )
    )
    assert not is_tts_engine_failure(
        replace(fish, error_type=ErrorType.TRANSLATION_FAILED, message="translation failed")
    )


def test_continue_rejects_wrong_state_already_edge_and_non_engine(tmp_path: Path) -> None:
    service, queue, filesystem = _service(tmp_path)

    async def _reject(job: Job, message: str) -> None:
        await filesystem.save_job(job)
        with pytest.raises(DomainError, match=message):
            await service.continue_with_edge(job.id)

    asyncio.run(
        _reject(
            _failed(
                error=ErrorType.TRANSLATION_FAILED,
                message="translation failed",
                stage="translating",
            ),
            "voice engine",
        )
    )
    asyncio.run(
        _reject(
            _failed(error=ErrorType.TIMEOUT, message="job exceeded the worker timeout (1800s)"),
            "voice engine",
        )
    )
    asyncio.run(_reject(_failed(engine="edge"), "already uses Edge"))
    asyncio.run(
        _reject(
            _failed(status=JobStatus.QUEUED, error=None, message=None, stage=None),
            "cannot continue",
        )
    )
    assert queue.job_ids == []


def test_fallback_scope_off_rejects_and_hides_the_button(tmp_path: Path) -> None:
    service, _, filesystem = _service(tmp_path, scope="off")
    job = _failed()

    async def _run_case() -> None:
        await filesystem.save_job(job)
        with pytest.raises(DomainError, match="not enabled"):
            await service.continue_with_edge(job.id)
        offered, _warning = service.edge_continue_view(job, None)
        assert offered is False

    asyncio.run(_run_case())


def test_second_click_does_not_rebuild_and_a_locked_job_is_rejected(tmp_path: Path) -> None:
    service, queue, filesystem = _service(tmp_path)
    job = _failed()

    async def _twice() -> Job:
        await filesystem.save_job(job)
        first = await service.continue_with_edge(job.id)
        second = await service.continue_with_edge(job.id)
        return second if second.id == first.id else first

    queued = asyncio.run(_twice())
    assert queued.status is JobStatus.QUEUED
    assert queued.tts_engine == "edge"
    assert queue.job_ids == [job.id]

    async def _locked() -> None:
        await filesystem.save_job(job)
        lock_path = tmp_path / "jobs" / job.id / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(DomainError, match="still running"):
                await service.continue_with_edge(job.id)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    asyncio.run(_locked())


def test_paused_generating_job_with_an_engine_error_can_continue(tmp_path: Path) -> None:
    service, queue, filesystem = _service(tmp_path)
    job = _failed(status=JobStatus.GENERATING_AUDIO)

    async def _go() -> Job:
        await filesystem.save_job(job)
        return await service.continue_with_edge(job.id)

    queued = asyncio.run(_go())
    assert queued.status is JobStatus.QUEUED
    assert queued.tts_engine == "edge"
    assert queue.job_ids == [job.id]


def test_fish_failure_keeps_chapter_one_and_resynthesizes_the_rest(tmp_path: Path) -> None:
    job = _failed(status=JobStatus.QUEUED, error=None, message=None, stage=None)
    fish_voices = [Voice(id="fish:story", language="en-US", label="Fish")]
    edge_voices = _voices_for("en-US")
    fish = _MarkedTTS(b"FISH", fish_voices, tmp_path / "fish", fail_on="night grew")
    edge = _MarkedTTS(b"EDGE", edge_voices, tmp_path / "edge")
    filesystem = FilesystemJobStorage(tmp_path)
    store = _Store(filesystem)
    asyncio.run(store.save(job))
    asyncio.run(filesystem.write_source(job.id, _STORY))
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        jobs=store,  # type: ignore[arg-type]
        languages=["en-US"],
        voices=fish_voices,
        tts=fish,
        max_chars=48,
        retry_policy=_NO_RETRY,
    )
    failed = _run(orchestrator, job, workspace, text=_STORY)
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.PROVIDER_RATE_LIMIT
    assert failed.error_stage == "generating_audio"
    assert translation.calls == []
    chapter_one = workspace / "chapters" / "0001.mp3"
    original = chapter_one.read_bytes()
    assert original.startswith(b"FISH")
    assert b"EDGE" not in original
    assert not (workspace / "chapters" / "0002.mp3").exists()
    assert b"FISH" in b"".join(path.read_bytes() for path in (workspace / "audio").glob("*.mp3"))

    service, _queue, _fs = _service(tmp_path, edge=edge)
    # The helper built a new filesystem rooted at tmp_path, which is the same tree.
    continued = asyncio.run(service.continue_with_edge(job.id))
    assert continued.tts_engine == "edge"
    assert continued.voice == "voice-en-US"
    assert continued.edge_fallback_note == LATER_CHAPTERS_WARNING
    assert chapter_one.read_bytes() == original
    manifest = FilesystemChapterManifest(workspace).load()
    assert manifest is not None
    assert [chapter.tts_engine for chapter in manifest.chapters] == ["fish", "edge", "edge"]
    assert manifest.chapters[0].note is None
    assert manifest.chapters[1].note
    assert manifest.chapters[0].status.value == "ready"

    orchestrator._tts = edge
    done = _run(orchestrator, continued, workspace, text=_STORY)
    assert done.status is JobStatus.COMPLETED
    assert done.tts_engine == "edge"
    assert chapter_one.read_bytes() == original
    chapter_two = (workspace / "chapters" / "0002.mp3").read_bytes()
    chapter_three = (workspace / "chapters" / "0003.mp3").read_bytes()
    assert chapter_two == b"EDGE" * (len(chapter_two) // 4)
    assert b"FISH" not in chapter_two
    assert b"FISH" not in chapter_three
    assert b"EDGE" in chapter_two
    finished = FilesystemChapterManifest(workspace).load()
    assert finished is not None
    assert [chapter.tts_engine for chapter in finished.chapters] == ["fish", "edge", "edge"]
    assert finished.chapters[0].status.value == "ready"
    assert all(chapter.status.value == "ready" for chapter in finished.chapters)


def test_single_span_remainder_is_only_edge(tmp_path: Path) -> None:
    job = _job(
        id=_JOB_ID,
        source_language="en-US",
        target_language="en-US",
        voice="fish:story",
        mode=JobMode.NARRATE,
        tts_engine="fish",
    )
    fish = _MarkedTTS(
        b"FISH",
        [Voice(id="fish:story", language="en-US", label="Fish")],
        tmp_path / "fish",
        fail_on="night grew",
    )
    edge = _MarkedTTS(b"EDGE", _voices_for("en-US"), tmp_path / "edge")
    filesystem = FilesystemJobStorage(tmp_path)
    store = _Store(filesystem)
    asyncio.run(store.save(job))
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        jobs=store,  # type: ignore[arg-type]
        languages=["en-US"],
        voices=[Voice(id="fish:story", language="en-US", label="Fish")],
        tts=fish,
        max_chars=60,
        retry_policy=_NO_RETRY,
    )
    failed = _run(orchestrator, job, workspace, text=_PLAIN)
    assert failed.status is JobStatus.FAILED
    assert (workspace / "audio").exists()
    assert any(b"FISH" in path.read_bytes() for path in (workspace / "audio").glob("*.mp3"))

    service, _, _ = _service(tmp_path, edge=edge)
    continued = asyncio.run(service.continue_with_edge(job.id))
    assert continued.edge_fallback_note == SINGLE_SPAN_WARNING
    orchestrator._tts = edge
    done = _run(orchestrator, continued, workspace, text=_PLAIN)
    assert done.status is JobStatus.COMPLETED
    merged = (workspace / "output.mp3").read_bytes()
    chapter = (workspace / "chapters" / "0001.mp3").read_bytes()
    assert b"FISH" not in merged
    assert b"FISH" not in chapter
    assert b"EDGE" in merged
    manifest = FilesystemChapterManifest(workspace).load()
    assert manifest is not None and len(manifest.chapters) == 1
    assert manifest.chapters[0].tts_engine == "edge"


def test_translate_chunks_are_not_repeated_after_continue(tmp_path: Path) -> None:
    job = _job(
        id=_JOB_ID,
        source_language="en-US",
        target_language="ja-JP",
        voice="fish:story",
        mode=JobMode.TRANSLATE,
        tts_engine="fish",
    )
    fish = _MarkedTTS(
        b"FISH",
        [Voice(id="fish:story", language="ja-JP", label="Fish")],
        tmp_path / "fish",
        fail_on="night grew",
    )
    edge = _MarkedTTS(b"EDGE", _voices_for("ja-JP"), tmp_path / "edge")
    translation = FakeTranslationProvider(["en-US", "ja-JP"])
    filesystem = FilesystemJobStorage(tmp_path)
    store = _Store(filesystem)
    asyncio.run(store.save(job))
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        jobs=store,  # type: ignore[arg-type]
        translation=translation,
        languages=["en-US", "ja-JP"],
        voices=[Voice(id="fish:story", language="ja-JP", label="Fish")],
        tts=fish,
        max_chars=48,
        retry_policy=_NO_RETRY,
    )
    failed = _run(orchestrator, job, workspace, text=_STORY)
    assert failed.status is JobStatus.FAILED
    translated = len(translation.calls)
    assert translated > 0
    original = (workspace / "chapters" / "0001.mp3").read_bytes()
    assert original.startswith(b"FISH")
    service, _, _ = _service(tmp_path, edge=edge)
    continued = asyncio.run(service.continue_with_edge(job.id))
    orchestrator._tts = edge
    done = _run(orchestrator, continued, workspace, text=_STORY)
    assert done.status is JobStatus.COMPLETED
    assert len(translation.calls) == translated
    assert (workspace / "chapters" / "0001.mp3").read_bytes() == original
    assert b"FISH" not in (workspace / "chapters" / "0002.mp3").read_bytes()


def test_budget_yield_after_continue_resumes_on_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    _patch_clock(monkeypatch, clock)
    job = _job(
        id=_JOB_ID,
        source_language="en-US",
        target_language="en-US",
        voice="fish:story",
        mode=JobMode.NARRATE,
        tts_engine="fish",
    )
    fish = _MarkedTTS(
        b"FISH",
        [Voice(id="fish:story", language="en-US", label="Fish")],
        tmp_path / "fish",
        fail_on="sun set",
    )
    edge = _ClockTTS(clock, _voices_for("en-US"), tmp_path / "edge", step=1)
    filesystem = FilesystemJobStorage(tmp_path)
    store = _Store(filesystem)
    asyncio.run(store.save(job))
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        jobs=store,  # type: ignore[arg-type]
        languages=["en-US"],
        voices=[Voice(id="fish:story", language="en-US", label="Fish")],
        tts=fish,
        max_chars=80,
        retry_policy=_NO_RETRY,
    )
    failed = _run(orchestrator, job, workspace, text=_STORY, deadline=100)
    assert failed.status is JobStatus.FAILED
    chapter_one = (workspace / "chapters" / "0001.mp3").read_bytes()
    service, _, _ = _service(tmp_path, edge=edge)
    continued = asyncio.run(service.continue_with_edge(job.id))
    clock.now = 0
    orchestrator._tts = edge
    yielded = _run(orchestrator, continued, workspace, text=_STORY, deadline=0.5)
    assert yielded.continue_requested is True
    assert yielded.tts_engine == "edge"
    assert yielded.status is not JobStatus.FAILED
    assert (workspace / "chapters" / "0001.mp3").read_bytes() == chapter_one
    clock.now = 0
    done = _run(orchestrator, yielded, workspace, text=_STORY, deadline=100)
    assert done.status is JobStatus.COMPLETED
    assert done.tts_engine == "edge"
    assert done.continue_requested is False
    assert (workspace / "chapters" / "0001.mp3").read_bytes() == chapter_one


def test_api_continue_with_edge_queues_and_rejects_a_translation_failure(
    tmp_path: Path,
) -> None:
    store = InMemoryJobStore()
    queue = InMemoryQueue()
    filesystem = FilesystemJobStorage(tmp_path)
    edge = FakeTTSProvider(_voices_for("ja-JP"), tmp_path / "edge")
    service = JobService(
        jobs=store,
        source_storage=InMemorySourceStorage(),
        queue=queue,
        output_bitrate_kbps=128,
        storage_path=tmp_path,
        disk_jobs=filesystem,
        tts_engines={"edge": edge, "fish": edge, "fake": edge},
        default_tts_engine="fake",
        fallback_scope="chapter",
        chapter_manifest_factory=FilesystemChapterManifest,
        checkpoint_storage_factory=CheckpointFilesystem,
    )
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    app.state.job_service = service
    job = _job(
        id=_JOB_ID,
        status=JobStatus.FAILED,
        source_language="en-US",
        target_language="ja-JP",
        voice="fish:story",
        tts_engine="fish",
        error_type=ErrorType.TTS_FAILED,
        message="tts connection failed",
        error_stage="generating_audio",
    )
    asyncio.run(filesystem.save_job(job))
    asyncio.run(store.save(job))
    with TestClient(app) as client:
        response = client.post(f"/api/jobs/{job.id}/continue-with-edge")
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        body = client.get(f"/api/jobs/{job.id}").json()
        assert body["tts_engine"] == "edge"
        assert body["continue_with_edge"] is False
        assert "no chapter breaks" in body["tts_mix_warning"]
        asyncio.run(
            filesystem.save_job(
                replace(
                    job,
                    error_type=ErrorType.TRANSLATION_FAILED,
                    message="translation failed",
                    error_stage="translating",
                    tts_engine="fish",
                    edge_fallback_note=None,
                )
            )
        )
        rejected = client.post(f"/api/jobs/{job.id}/continue-with-edge")
        assert rejected.status_code == 409
        assert rejected.json()["message"] == "job did not fail because of the voice engine"
