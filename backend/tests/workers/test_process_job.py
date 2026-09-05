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
from app.infrastructure.rq_queue import RQ_PROCESS_JOB
from app.providers.translation.fake import FakeTranslationProvider
from app.providers.tts.fake import FakeTTSProvider
from app.workers.runner import process_job
from tests.fakes import InMemoryJobStore, InMemorySourceStorage


def test_rq_process_job_path_resolves() -> None:
    module_path, name = RQ_PROCESS_JOB.rsplit(".", 1)
    assert callable(getattr(import_module(module_path), name))


def test_process_job_completes_with_fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryJobStore()
    source = InMemorySourceStorage()
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
    asyncio.run(store.save(job))
    asyncio.run(source.write_source(job.id, "Hello for the worker."))

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
    monkeypatch.setattr("app.workers.runner.build_job_store", lambda _: store)
    monkeypatch.setattr("app.workers.runner.build_source_storage", lambda _: source)
    monkeypatch.setattr("app.workers.runner.build_orchestrator", lambda _: orchestrator)

    process_job(job.id)

    done = asyncio.run(store.get(job.id))
    assert done is not None
    assert done.status is JobStatus.COMPLETED
    output = tmp_path / "jobs" / job.id / "output.mp3"
    assert output.is_file()
    assert output.read_bytes() == b"FAKEAUDIO"
