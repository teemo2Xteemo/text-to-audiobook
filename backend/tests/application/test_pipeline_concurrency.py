from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.chunking import chunk_text
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.infrastructure.checkpoint_fs import CheckpointFilesystem
from tests.application.test_pipeline import (
    FakeAudioProcessor,
    FakeLanguageDetector,
    FakeNarrationProcessor,
    FakeTranslationProvider,
    FakeTTSProvider,
    _job,
    _RecordingSleep,
    _seed_completed_chunks,
    _voices_for,
)
from tests.fakes import InMemoryJobStore


class ConcurrencyTrackingTranslation(FakeTranslationProvider):
    def __init__(self, languages: Sequence[str] | None = None, delay: float = 0.02) -> None:
        super().__init__(languages)
        self.delay = delay
        self.active_count = 0
        self.peak_concurrency = 0

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        self.active_count += 1
        if self.active_count > self.peak_concurrency:
            self.peak_concurrency = self.active_count
        try:
            await asyncio.sleep(self.delay)
            return await super().translate(text, source_language, target_language)
        finally:
            self.active_count -= 1


class ConcurrencyTrackingTTS(FakeTTSProvider):
    def __init__(
        self,
        voices: list[Voice],
        output_dir: Path,
        delay: float = 0.02,
    ) -> None:
        super().__init__(voices, output_dir)
        self.delay = delay
        self.active_count = 0
        self.peak_concurrency = 0

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        self.active_count += 1
        if self.active_count > self.peak_concurrency:
            self.peak_concurrency = self.active_count
        try:
            await asyncio.sleep(self.delay)
            return await super().synthesize(text, language, voice, settings)
        finally:
            self.active_count -= 1


def _concurrency_orchestrator(
    tmp_path: Path,
    *,
    job: Job,
    translation: FakeTranslationProvider | None = None,
    tts: FakeTTSProvider | None = None,
    jobs: InMemoryJobStore | None = None,
    translation_concurrency: int = 1,
    tts_concurrency: int = 1,
    max_chars: int = 15,
) -> tuple[PipelineOrchestrator, Path, InMemoryJobStore]:
    workspace = tmp_path / "jobs" / job.id
    langs = ["en-US", "vi-VN"]
    voice_list = _voices_for(job.target_language)
    trans = translation or FakeTranslationProvider(langs)
    tts_prov = tts or FakeTTSProvider(voice_list, tmp_path / "tts-scratch")
    job_store = jobs or InMemoryJobStore()
    orch = PipelineOrchestrator(
        translation=trans,
        tts=tts_prov,
        narration=FakeNarrationProcessor(),
        detector=FakeLanguageDetector(),
        audio=FakeAudioProcessor(),
        jobs=job_store,
        checkpoint_storage_factory=CheckpointFilesystem,
        max_chars=max_chars,
        translation_concurrency=translation_concurrency,
        tts_concurrency=tts_concurrency,
        sleep=_RecordingSleep(),
    )
    return orch, workspace, job_store


@pytest.mark.anyio
async def test_concurrent_translation_reaches_expected_peak_concurrency(tmp_path: Path) -> None:
    job = _job(
        id="job-conc-001",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="vi-VN",
        output_format=OutputFormat.MP3,
    )
    translation = ConcurrencyTrackingTranslation(["en-US", "vi-VN"], delay=0.03)
    # Story produces 5 chunks with max_chars=15
    story = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five."
    orchestrator, workspace, _ = _concurrency_orchestrator(
        tmp_path,
        job=job,
        translation=translation,
        translation_concurrency=3,
        tts_concurrency=1,
        max_chars=15,
    )

    result = await orchestrator.run(job, story, workspace=workspace)
    assert result.status is JobStatus.COMPLETED
    assert translation.peak_concurrency == 3
    assert len(translation.calls) >= 4


@pytest.mark.anyio
async def test_concurrent_tts_reaches_expected_peak_concurrency(tmp_path: Path) -> None:
    job = _job(
        id="job-conc-002",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="vi-VN",
        output_format=OutputFormat.MP3,
    )
    voice_list = _voices_for(job.target_language)
    tts = ConcurrencyTrackingTTS(voice_list, tmp_path / "tts-scratch", delay=0.03)
    story = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five."
    orchestrator, workspace, _ = _concurrency_orchestrator(
        tmp_path,
        job=job,
        tts=tts,
        translation_concurrency=1,
        tts_concurrency=2,
        max_chars=15,
    )

    result = await orchestrator.run(job, story, workspace=workspace)
    assert result.status is JobStatus.COMPLETED
    assert tts.peak_concurrency == 2
    assert (workspace / "output.mp3").is_file()


@pytest.mark.anyio
async def test_concurrent_progress_increases_monotonically(tmp_path: Path) -> None:
    job = _job(
        id="job-conc-003",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="vi-VN",
        output_format=OutputFormat.MP3,
    )

    class MonotonicStore(InMemoryJobStore):
        def __init__(self) -> None:
            super().__init__()
            self.stages_progress: dict[JobStatus, list[int]] = {}

        async def save(self, saved_job: Job) -> None:
            self.stages_progress.setdefault(saved_job.status, []).append(saved_job.chunk_current)
            await super().save(saved_job)

    store = MonotonicStore()
    story = "Chunk A is here. Chunk B is here. Chunk C is here. Chunk D is here."
    orchestrator, workspace, _ = _concurrency_orchestrator(
        tmp_path,
        job=job,
        jobs=store,
        translation_concurrency=3,
        tts_concurrency=2,
        max_chars=18,
    )

    result = await orchestrator.run(job, story, workspace=workspace)
    assert result.status is JobStatus.COMPLETED

    for stage in [JobStatus.TRANSLATING, JobStatus.GENERATING_AUDIO]:
        progress_seq = store.stages_progress.get(stage, [])
        assert len(progress_seq) > 0
        # Check non-decreasing
        for i in range(len(progress_seq) - 1):
            assert progress_seq[i] <= progress_seq[i + 1]
        assert progress_seq[-1] == result.chunk_total


@pytest.mark.anyio
async def test_concurrent_translation_failure_cancels_cleanly(tmp_path: Path) -> None:
    job = _job(
        id="job-conc-004",
        status=JobStatus.QUEUED,
        source_language="en-US",
        target_language="vi-VN",
        output_format=OutputFormat.MP3,
    )

    class FlakyConcurrentTranslation(FakeTranslationProvider):
        async def translate(self, text: str, source_language: str, target_language: str) -> str:
            if "fail" in text:
                raise DomainError(ErrorType.TRANSLATION_FAILED, "simulated failure")
            await asyncio.sleep(0.05)
            return await super().translate(text, source_language, target_language)

    story = "First ok text. Second fail text. Third ok text. Fourth ok text."
    orchestrator, workspace, _ = _concurrency_orchestrator(
        tmp_path,
        job=job,
        translation=FlakyConcurrentTranslation(["en-US", "vi-VN"]),
        translation_concurrency=3,
        max_chars=18,
    )

    result = await orchestrator.run(job, story, workspace=workspace)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.TRANSLATION_FAILED


@pytest.mark.anyio
async def test_concurrent_resume_skips_completed_chunks(tmp_path: Path) -> None:
    story = "Chunk one text. Chunk two text. Chunk three text. Chunk four text."
    chunks = chunk_text(story, max_chars=18)
    assert len(chunks) == 4

    job = _job(
        id="job-conc-005",
        status=JobStatus.GENERATING_AUDIO,
        source_language="en-US",
        target_language="vi-VN",
        chunk_current=2,
        chunk_total=4,
        output_format=OutputFormat.MP3,
    )
    voice_list = _voices_for(job.target_language)
    tts = FakeTTSProvider(voice_list, tmp_path / "tts-scratch")
    orchestrator, workspace, _ = _concurrency_orchestrator(
        tmp_path,
        job=job,
        tts=tts,
        translation_concurrency=2,
        tts_concurrency=2,
        max_chars=18,
    )
    _seed_completed_chunks(workspace, chunks, 2)

    result = await orchestrator.run(job, story, workspace=workspace)
    assert result.status is JobStatus.COMPLETED
    # Only chunk 3 and chunk 4 should have been synthesized by TTS
    assert len(tts.calls) == 2
    assert (workspace / "output.mp3").is_file()
