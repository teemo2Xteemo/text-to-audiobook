from __future__ import annotations

import asyncio
from importlib import import_module
from pathlib import Path

import pytest

from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.application.pipeline.passthrough import (
    FixedLanguageDetector,
    PassthroughNarrationProcessor,
)
from app.config.factory import build_orchestrator
from app.config.settings import Settings
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.infrastructure.checkpoint_fs import CheckpointFilesystem
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.rq_queue import RQ_PROCESS_JOB
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.translation.nllb import NllbTranslationProvider
from app.providers.translation.ollama import OllamaTranslationProvider
from app.providers.tts.fake import FakeTTSProvider
from app.workers.runner import boot, process_job
from tests.fakes import InMemoryJobStore


def _persist_job(tmp_path: Path, job: Job, text: str) -> None:
    filesystem = FilesystemJobStorage(tmp_path)
    asyncio.run(filesystem.write_source(job.id, text))
    asyncio.run(filesystem.save_job(job))


def test_rq_process_job_path_resolves() -> None:
    module_path, name = RQ_PROCESS_JOB.rsplit(".", 1)
    assert callable(getattr(import_module(module_path), name))


def test_workers_package_exposes_main_entry() -> None:
    main = import_module("app.workers.__main__")
    assert callable(getattr(main, "boot"))


def test_process_job_completes_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryJobStore()
    job = Job(
        id="22222222-2222-2222-2222-222222222222",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="ja-JP",
        voice="fake-ja-JP-a",
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )
    _persist_job(tmp_path, job, "Hello for the worker.")

    settings = Settings(_env_file=None, storage_path=tmp_path)
    orchestrator = PipelineOrchestrator(
        translation=FakeTranslationProvider(),
        tts=FakeTTSProvider(output_dir=tmp_path / "tts"),
        narration=PassthroughNarrationProcessor(),
        detector=FixedLanguageDetector(),
        audio=FakeAudioProcessor(),
        jobs=store,
        checkpoint_storage_factory=CheckpointFilesystem,
    )

    monkeypatch.setattr("app.workers.runner.Settings", lambda **_: settings)
    monkeypatch.setattr("app.workers.runner.build_orchestrator", lambda _: orchestrator)

    process_job(job.id)

    done = asyncio.run(store.get(job.id))
    assert done is not None
    assert done.status is JobStatus.COMPLETED
    output = tmp_path / "jobs" / job.id / "output.mp3"
    assert output.is_file()
    assert output.read_bytes() == b"FAKEAUDIO"


def test_process_job_completed_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(
        id="33333333-3333-3333-3333-333333333333",
        status=JobStatus.COMPLETED,
        source_language="en-US",
        target_language="ja-JP",
        voice="fake-ja-JP-a",
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )
    _persist_job(tmp_path, job, "Already done.")
    settings = Settings(_env_file=None, storage_path=tmp_path)
    runs: list[JobStatus] = []

    class _Capture:
        async def run(self, captured: Job, text: str, *, workspace: Path) -> Job:
            del text, workspace
            runs.append(captured.status)
            return captured

    monkeypatch.setattr("app.workers.runner.Settings", lambda **_: settings)
    monkeypatch.setattr("app.workers.runner.build_orchestrator", lambda _: _Capture())

    process_job(job.id)
    assert runs == []
    done = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job.id))
    assert done is not None
    assert done.status is JobStatus.COMPLETED


def test_process_job_failed_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(
        id="55555555-5555-5555-5555-555555555555",
        status=JobStatus.FAILED,
        source_language="en-US",
        target_language="ja-JP",
        voice="fake-ja-JP-a",
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )
    _persist_job(tmp_path, job, "Failed story.")
    settings = Settings(_env_file=None, storage_path=tmp_path)
    runs: list[JobStatus] = []

    class _Capture:
        async def run(self, captured: Job, text: str, *, workspace: Path) -> Job:
            del text, workspace
            runs.append(captured.status)
            return captured

    monkeypatch.setattr("app.workers.runner.Settings", lambda **_: settings)
    monkeypatch.setattr("app.workers.runner.build_orchestrator", lambda _: _Capture())

    process_job(job.id)
    assert runs == []
    done = asyncio.run(FilesystemJobStorage(tmp_path).get_job(job.id))
    assert done is not None
    assert done.status is JobStatus.FAILED


def test_process_job_resumes_generating_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.application.pipeline.checkpoint import (
        STAGE_NARRATED,
        STAGE_NORMALIZED,
        STAGE_TRANSLATED,
        STAGE_TTS,
        CheckpointStore,
    )
    from app.domain.chunking import chunk_text

    store = InMemoryJobStore()
    job = Job(
        id="44444444-4444-4444-4444-444444444444",
        status=JobStatus.GENERATING_AUDIO,
        source_language="en-US",
        target_language="ja-JP",
        voice="fake-ja-JP-a",
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
    )
    text = "Alpha is first. Bravo is second. Charlie is third. Delta is fourth. Echo is fifth."
    chunks = chunk_text(text, max_chars=20)
    assert len(chunks) == 5
    asyncio.run(store.save(job))
    _persist_job(tmp_path, job, text)

    settings = Settings(_env_file=None, storage_path=tmp_path)
    workspace = tmp_path / "jobs" / job.id
    workspace.mkdir(parents=True, exist_ok=True)
    checkpoints = CheckpointStore(workspace, storage=CheckpointFilesystem(workspace))
    for chunk in chunks[:2]:
        translated = workspace / "chunks" / f"{chunk.id}.translated.txt"
        translated.parent.mkdir(parents=True, exist_ok=True)
        translated.write_text(f"seed-{chunk.id}", encoding="utf-8")
        checkpoints.record(chunk.id, STAGE_TRANSLATED, translated)
        narrated = workspace / "chunks" / f"{chunk.id}.narrated.txt"
        narrated.write_text(f"seed-narrated-{chunk.id}", encoding="utf-8")
        checkpoints.record(chunk.id, STAGE_NARRATED, narrated)
        raw = workspace / "audio" / f"{chunk.id}.mp3"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"SEEDAUDIO")
        checkpoints.record(chunk.id, STAGE_TTS, raw)
        normalized = workspace / "audio" / f"{chunk.id}.normalized.mp3"
        normalized.write_bytes(b"SEEDAUDIO")
        checkpoints.record(chunk.id, STAGE_NORMALIZED, normalized)

    translation = FakeTranslationProvider(["en-US", "ja-JP"])
    tts = FakeTTSProvider(output_dir=tmp_path / "tts")
    orchestrator = PipelineOrchestrator(
        translation=translation,
        tts=tts,
        narration=PassthroughNarrationProcessor(),
        detector=FixedLanguageDetector(),
        audio=FakeAudioProcessor(),
        jobs=store,
        checkpoint_storage_factory=CheckpointFilesystem,
        max_chars=20,
    )
    monkeypatch.setattr("app.workers.runner.Settings", lambda **_: settings)
    monkeypatch.setattr("app.workers.runner.build_orchestrator", lambda _: orchestrator)

    process_job(job.id)

    done = asyncio.run(store.get(job.id))
    assert done is not None
    assert done.status is JobStatus.COMPLETED
    assert chunks[0].text not in [call[0] for call in translation.calls]
    assert chunks[1].text not in [call[0] for call in translation.calls]
    assert sum(1 for call in tts.calls if chunks[0].text in call[0]) == 0
    assert sum(1 for call in tts.calls if chunks[2].text in call[0]) == 1
    assert (tmp_path / "jobs" / job.id / "output.mp3").is_file()


def test_boot_recovers_then_listens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recovered: list[str] = []
    listened: list[bool] = []

    class _Service:
        async def recover_in_progress(self) -> list[str]:
            recovered.append("ok")
            return ["job-id"]

    monkeypatch.setattr(
        "app.workers.runner.Settings", lambda **_: Settings(_env_file=None, storage_path=tmp_path)
    )
    monkeypatch.setattr("app.workers.runner.build_job_service", lambda _: _Service())
    monkeypatch.setattr("app.workers.runner._listen", lambda settings: listened.append(True))

    boot()
    assert recovered == ["ok"]
    assert listened == [True]


def test_listen_uses_settings_timeout_and_failure_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers.rq_failure import handle_worker_exception

    captured: dict[str, object] = {}

    class _FakeRedis:
        @classmethod
        def from_url(cls, url: str) -> object:
            captured["redis_url"] = url
            return object()

    class _FakeQueue:
        def __init__(self, name: str, connection: object, default_timeout: int) -> None:
            captured["queue"] = (name, default_timeout)

    class _FakeWorker:
        def __init__(
            self,
            queues: object,
            connection: object = None,
            exception_handlers: object = None,
        ) -> None:
            captured["handlers"] = exception_handlers

        def work(self) -> None:
            captured["work"] = True

    settings = Settings(_env_file=None, storage_path=tmp_path, rq_job_timeout_seconds=2400)
    monkeypatch.setattr("app.workers.runner.Redis", _FakeRedis)
    monkeypatch.setattr("app.workers.runner.Queue", _FakeQueue)
    monkeypatch.setattr("app.workers.runner.Worker", _FakeWorker)

    from app.workers.runner import _listen

    _listen(settings)
    assert captured["queue"] == ("jobs", 2400)
    assert captured["handlers"] == [handle_worker_exception]
    assert captured["work"] is True


def _queued_job(job_id: str, *, provider: str | None, model: str | None) -> Job:
    return Job(
        id=job_id,
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="ja-JP",
        voice=None,
        speed=1.0,
        output_format=OutputFormat.MP3,
        output_bitrate_kbps=128,
        translation_provider=provider,
        translation_model=model,
    )


def _capture_pin(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
) -> dict[str, object]:
    captured: dict[str, object] = {}
    constructed: list[str] = []
    real_init = NllbTranslationProvider.__init__

    def _spy_nllb(self: NllbTranslationProvider, *args: object, **kwargs: object) -> None:
        constructed.append("nllb")
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(NllbTranslationProvider, "__init__", _spy_nllb)

    def _build(effective: Settings) -> PipelineOrchestrator:
        orchestrator = build_orchestrator(effective)
        captured["provider"] = effective.translation_provider
        captured["model"] = effective.ollama_translation_model
        captured["nllb_model"] = effective.nllb_model_id
        captured["translation"] = orchestrator._translation
        captured["nllb_constructed"] = list(constructed)

        async def _stop(job: Job, text: str, *, workspace: Path) -> Job:
            del text, workspace
            return job

        orchestrator.run = _stop  # type: ignore[method-assign]
        return orchestrator

    monkeypatch.setattr("app.workers.runner.Settings", lambda **_: settings)
    monkeypatch.setattr("app.workers.runner.build_orchestrator", _build)
    return captured


def test_process_job_uses_ollama_pin_when_env_is_nllb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _queued_job(
        "44444444-4444-4444-4444-444444444444",
        provider="ollama",
        model="translategemma:4b",
    )
    _persist_job(tmp_path, job, "Pinned story.")
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="nllb",
        nllb_model_id="facebook/nllb-200-distilled-600M",
    )
    captured = _capture_pin(monkeypatch, settings)
    process_job(job.id)
    assert captured["provider"] == "ollama"
    assert captured["model"] == "translategemma:4b"
    assert isinstance(captured["translation"], OllamaTranslationProvider)
    assert captured["nllb_constructed"] == []


def test_process_job_without_pin_follows_current_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "55555555-5555-5555-5555-555555555555"
    pinned = _queued_job(job_id, provider="fake", model="fake")
    _persist_job(tmp_path, pinned, "Legacy story.")
    payload = pinned.to_dict()
    payload.pop("translation_provider")
    payload.pop("translation_model")
    status_path = tmp_path / "jobs" / job_id / "status.json"
    status_path.write_text(__import__("json").dumps(payload), encoding="utf-8")
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="ollama",
        ollama_translation_model="translategemma:12b",
    )
    captured = _capture_pin(monkeypatch, settings)
    process_job(job_id)
    assert captured["provider"] == "ollama"
    assert captured["model"] == "translategemma:12b"


def test_process_job_half_pin_follows_current_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = "66666666-6666-6666-6666-666666666666"
    pinned = _queued_job(job_id, provider="fake", model="fake")
    _persist_job(tmp_path, pinned, "Half pin.")
    payload = pinned.to_dict()
    payload.pop("translation_model")
    (tmp_path / "jobs" / job_id / "status.json").write_text(
        __import__("json").dumps(payload),
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        storage_path=tmp_path,
        translation_provider="nllb",
        nllb_model_id="facebook/nllb-200-distilled-1.3B",
    )
    captured = _capture_pin(monkeypatch, settings)
    process_job(job_id)
    assert captured["provider"] == "nllb"
    assert captured["nllb_model"] == "facebook/nllb-200-distilled-1.3B"


def test_listen_spawns_extra_processes_when_worker_concurrency_above_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.workers import runner

    events: list[str] = []

    class _FakeProcess:
        def __init__(self, *, target: object, args: tuple[object, ...], daemon: bool) -> None:
            assert target is runner._run_worker
            assert len(args) == 1
            assert daemon is True
            events.append("init")

        def start(self) -> None:
            events.append("start")

        def is_alive(self) -> bool:
            return True

        def terminate(self) -> None:
            events.append("terminate")

        def join(self, timeout: float | None = None) -> None:
            del timeout
            events.append("join")

    monkeypatch.setattr("app.workers.runner.Process", _FakeProcess)
    monkeypatch.setattr(
        "app.workers.runner._run_worker",
        lambda settings: events.append(f"work:{settings.worker_concurrency}"),
    )

    settings = Settings(_env_file=None, storage_path=tmp_path, worker_concurrency=3)
    runner._listen(settings)
    assert events == [
        "init",
        "start",
        "init",
        "start",
        "work:3",
        "terminate",
        "join",
        "terminate",
        "join",
    ]
