from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.application.pipeline.checkpoint import (
    STAGE_NARRATED,
    STAGE_NORMALIZED,
    STAGE_TRANSLATED,
    STAGE_TTS,
    CheckpointStore,
)
from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.application.pipeline.parse_srt import parse_srt_cues
from app.domain.audio import Voice
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.retry import RetryPolicy
from app.infrastructure.checkpoint_fs import CheckpointFilesystem
from tests.fakes import (
    FakeAudioProcessor,
    FakeLanguageDetector,
    FakeNarrationProcessor,
    FakeTranslationProvider,
    FakeTTSProvider,
    InMemoryJobStore,
)


def _voices_for(*languages: str) -> list[Voice]:
    return [
        Voice(id=f"voice-{language}", language=language, label=language) for language in languages
    ]


def _make_job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "id": "job-srt-default",
        "status": JobStatus.QUEUED,
        "source_language": "en-US",
        "target_language": "en-US",
        "voice": None,
        "speed": 1.0,
        "output_format": OutputFormat.MP3,
        "output_bitrate_kbps": 128,
    }
    values.update(overrides)
    return Job(**values)  # type: ignore[arg-type]


def _build_orchestrator(
    tmp_path: Path,
    *,
    job: Job,
    translation: FakeTranslationProvider | None = None,
    tts: FakeTTSProvider | None = None,
    audio: FakeAudioProcessor | None = None,
    narration: FakeNarrationProcessor | None = None,
    detector: FakeLanguageDetector | None = None,
    jobs: InMemoryJobStore | None = None,
) -> tuple[
    PipelineOrchestrator,
    Path,
    FakeTranslationProvider,
    FakeTTSProvider,
    FakeAudioProcessor,
    InMemoryJobStore,
]:
    workspace = tmp_path / "work" / job.id
    trans = translation or FakeTranslationProvider([job.source_language, job.target_language])
    tts_prov = tts or FakeTTSProvider(_voices_for(job.target_language), tmp_path / "tts-scratch")
    audio_proc = audio or FakeAudioProcessor()
    narr = narration or FakeNarrationProcessor()
    det = detector or FakeLanguageDetector(language_code=job.source_language)
    store = jobs or InMemoryJobStore()

    orchestrator = PipelineOrchestrator(
        translation=trans,
        tts=tts_prov,
        narration=narr,
        detector=det,
        audio=audio_proc,
        jobs=store,
        checkpoint_storage_factory=CheckpointFilesystem,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=0.01),
    )
    return orchestrator, workspace, trans, tts_prov, audio_proc, store


def _run(
    orchestrator: PipelineOrchestrator,
    job: Job,
    workspace: Path,
    text: str,
) -> Job:
    return asyncio.run(orchestrator.run(job, text, workspace=workspace))


def test_srt_pipeline_generates_aligned_audio_and_subtitles(tmp_path: Path) -> None:
    srt_text = (
        "1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nSecond line.\n"
    )
    job = _make_job(
        id="job-srt-1",
        source_language="en-US",
        target_language="en-US",
    )
    orchestrator, workspace, _, _, audio, _ = _build_orchestrator(tmp_path, job=job)
    audio.set_duration("chunk-001.normalized.mp3", 1500)
    audio.set_duration("chunk-002.normalized.mp3", 1200)

    result = _run(orchestrator, job, workspace, srt_text)

    assert result.status is JobStatus.COMPLETED

    output_audio = workspace / "output.mp3"
    assert output_audio.is_file()
    assert output_audio.stat().st_size > 0
    assert audio.full_encode_flags[-1] is True
    assert audio.full_encode_flags.count(True) == 1

    output_srt = workspace / "output.srt"
    assert output_srt.is_file()
    srt_content = output_srt.read_text(encoding="utf-8")
    assert "Hello world." in srt_content
    assert "Second line." in srt_content

    cues = parse_srt_cues(srt_content)
    assert len(cues) == 2
    # Chunk 1 starts at nominal 1000ms, duration 1500ms -> ends at 2500ms
    assert cues[0].start_ms == 1000
    assert cues[0].end_ms == 2500
    assert cues[0].text == "Hello world."

    # Chunk 2 nominal start is 5000ms. Available pause is 5000 - 2500 = 2500ms.
    # No drift accumulated, so Chunk 2 starts at 5000ms, duration 1200ms -> ends at 6200ms.
    assert cues[1].start_ms == 5000
    assert cues[1].end_ms == 6200
    assert cues[1].text == "Second line."

    # Silence verification: initial silence (1000ms) + gap silence (2500ms)
    assert len(audio.silence_calls) == 2
    assert audio.silence_calls[0][0] == 1000
    assert audio.silence_calls[1][0] == 2500


def test_srt_pipeline_with_translation(tmp_path: Path) -> None:
    srt_text = (
        "1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nSecond line.\n"
    )
    job = _make_job(
        id="job-srt-2",
        source_language="en-US",
        target_language="ko-KR",
        voice=None,
    )
    orchestrator, workspace, translation, _, audio, _ = _build_orchestrator(tmp_path, job=job)
    audio.set_duration("chunk-001.normalized.mp3", 1000)
    audio.set_duration("chunk-002.normalized.mp3", 1000)

    result = _run(orchestrator, job, workspace, srt_text)

    assert result.status is JobStatus.COMPLETED
    assert len(translation.calls) == 2
    assert translation.calls[0][1] == "en-US"
    assert translation.calls[0][2] == "ko-KR"

    output_srt = workspace / "output.srt"
    assert output_srt.is_file()
    srt_content = output_srt.read_text(encoding="utf-8")
    assert "[ko-KR] Hello world." in srt_content
    assert "[ko-KR] Second line." in srt_content


def test_srt_pipeline_drift_absorption_and_tempo(tmp_path: Path) -> None:
    # 2 cues with zero gap, where chunk 1 overruns by 4000ms (> 3000ms safety threshold)
    srt_text = (
        "1\n00:00:01,000 --> 00:00:02,000\nFast speech cue one.\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\nFast speech cue two.\n"
    )
    job = _make_job(
        id="job-srt-3",
        source_language="en-US",
        target_language="en-US",
    )
    orchestrator, workspace, _, _, audio, _ = _build_orchestrator(tmp_path, job=job)
    # Chunk 1 takes 5000ms (overruns by 4000ms past 2000ms boundary)
    audio.set_duration("chunk-001.normalized.mp3", 5000)
    audio.set_duration("chunk-002.normalized.mp3", 1000)

    result = _run(orchestrator, job, workspace, srt_text)

    assert result.status is JobStatus.COMPLETED
    # Chunk 2 experiences accumulated drift of 4000ms > 3000ms, triggering tempo scaling
    assert len(audio.tempo_calls) == 1
    assert audio.tempo_calls[0][2] == pytest.approx(1.12)


def test_srt_pipeline_resumes_without_regenerating_tts(tmp_path: Path) -> None:
    srt_text = (
        "1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nSecond line.\n"
    )
    job = _make_job(
        id="job-srt-4",
        source_language="en-US",
        target_language="en-US",
    )
    orchestrator, workspace, _, tts, _, _ = _build_orchestrator(tmp_path, job=job)

    # Pre-seed chunk 1 checkpoints
    workspace.mkdir(parents=True, exist_ok=True)
    c_store = CheckpointStore(storage=CheckpointFilesystem(workspace))

    trans_1 = workspace / "chunks" / "chunk-001.translated.txt"
    trans_1.parent.mkdir(parents=True, exist_ok=True)
    trans_1.write_text("Hello world.", encoding="utf-8")
    c_store.record("chunk-001", STAGE_TRANSLATED, trans_1)

    narr_1 = workspace / "chunks" / "chunk-001.narrated.txt"
    narr_1.write_text("Hello world.", encoding="utf-8")
    c_store.record("chunk-001", STAGE_NARRATED, narr_1)

    raw_1 = workspace / "audio" / "chunk-001.mp3"
    raw_1.parent.mkdir(parents=True, exist_ok=True)
    raw_1.write_bytes(b"FAKE_TTS_1")
    c_store.record("chunk-001", STAGE_TTS, raw_1)

    norm_1 = workspace / "audio" / "chunk-001.normalized.mp3"
    norm_1.write_bytes(b"FAKE_NORM_1")
    c_store.record("chunk-001", STAGE_NORMALIZED, norm_1)

    result = _run(orchestrator, job, workspace, srt_text)

    assert result.status is JobStatus.COMPLETED
    # TTS should only be called for chunk-002, not chunk-001
    assert len(tts.calls) == 1
    assert tts.calls[0][0] == "Second line."


def test_plain_text_pipeline_does_not_create_output_srt(tmp_path: Path) -> None:
    plain_text = "Alpha is first. Bravo is second. Charlie is third."
    job = _make_job(
        id="job-plain-1",
        source_language="en-US",
        target_language="en-US",
    )
    orchestrator, workspace, _, _, audio, _ = _build_orchestrator(tmp_path, job=job)

    result = _run(orchestrator, job, workspace, plain_text)

    assert result.status is JobStatus.COMPLETED
    assert (workspace / "output.mp3").is_file()
    # Plain text pipeline must NOT create output.srt
    assert not (workspace / "output.srt").exists()
    # Plain text pipeline must NOT call generate_silence
    assert len(audio.silence_calls) == 0
