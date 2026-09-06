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
from app.config.settings import Settings
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.infrastructure.fs_storage import FilesystemJobStorage
from app.infrastructure.rq_queue import RQ_PROCESS_JOB
from app.providers.translation.fake import FakeTranslationProvider
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
    checkpoints = CheckpointStore(workspace)
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
