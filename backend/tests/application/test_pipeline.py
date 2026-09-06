from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path

import pytest

from app.application.pipeline.checkpoint import (
    STAGE_NARRATED,
    STAGE_NORMALIZED,
    STAGE_TRANSLATED,
    STAGE_TTS,
    CheckpointStore,
)
from app.application.pipeline.conservative_narration import ConservativeNarrationProcessor
from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.chunking import chunk_text
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.languages import AUTO_SOURCE_LANGUAGE
from app.domain.ports import NarrationProcessor
from app.domain.retry import RetryPolicy
from tests.fakes import (
    FakeAudioProcessor,
    FakeLanguageDetector,
    FakeNarrationProcessor,
    FakeTranslationProvider,
    FakeTTSProvider,
    InMemoryJobStore,
)

THREE_SENTENCES = "Alpha is first. Bravo is second. Charlie is third."
FOUR_SENTENCES = "Alpha is first. Bravo is second. Charlie is third. Delta is fourth."
FIVE_SENTENCES = (
    "Alpha is first. Bravo is second. Charlie is third. Delta is fourth. Echo is fifth."
)
PIPELINE_DIR = Path(__file__).resolve().parents[2] / "app" / "application" / "pipeline"
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "edge_tts",
        "fastapi",
        "ffmpeg",
        "httpx",
        "redis",
        "rq",
        "transformers",
        "torch",
        "langdetect",
        "uvicorn",
    }
)
LANGUAGE_LITERALS = ("zh-CN", "vi-VN", "ja-JP", "zho", "NamMinh")


class RecordingJobStore(InMemoryJobStore):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[JobStatus] = []

    async def save(self, job: Job) -> None:
        self.statuses.append(job.status)
        await super().save(job)


class _RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class _FlakyByTextTTS(FakeTTSProvider):
    def __init__(
        self,
        voices: list[Voice],
        output_dir: Path,
        *,
        fail_on_text: str,
        fail_times: int = 1,
        error: Exception | None = None,
    ) -> None:
        super().__init__(voices, output_dir)
        self.fail_on_text = fail_on_text
        self.fail_times = fail_times
        self._failures = 0
        self.error = error or DomainError(ErrorType.TTS_FAILED, "tts failed")

    async def synthesize(
        self, text: str, language: str, voice: str, settings: TTSSettings
    ) -> AudioArtifact:
        if self.fail_on_text in text and self._failures < self.fail_times:
            self.calls.append((text, language, voice, settings))
            self._failures += 1
            raise self.error
        return await super().synthesize(text, language, voice, settings)


def _job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": JobStatus.QUEUED,
        "source_language": "en-US",
        "target_language": "ko-KR",
        "voice": None,
        "speed": 1.0,
        "output_format": OutputFormat.MP3,
        "output_bitrate_kbps": 128,
    }
    values.update(overrides)
    return Job(**values)  # type: ignore[arg-type]


def _voices_for(*languages: str) -> list[Voice]:
    return [
        Voice(id=f"voice-{language}", language=language, label=language) for language in languages
    ]


def _orchestrator(
    tmp_path: Path,
    *,
    job: Job,
    languages: list[str] | None = None,
    voices: list[Voice] | None = None,
    detector: FakeLanguageDetector | None = None,
    translation: FakeTranslationProvider | None = None,
    narration: NarrationProcessor | None = None,
    tts: FakeTTSProvider | None = None,
    audio: FakeAudioProcessor | None = None,
    jobs: InMemoryJobStore | None = None,
    max_chars: int | None = None,
    retry_policy: RetryPolicy | None = None,
    sleep: _RecordingSleep | None = None,
) -> tuple[
    PipelineOrchestrator,
    Path,
    FakeTranslationProvider,
    NarrationProcessor,
    FakeTTSProvider,
    FakeLanguageDetector,
    FakeAudioProcessor,
    InMemoryJobStore,
]:
    workspace = tmp_path / "jobs" / job.id
    langs = languages or ["en-US", "ko-KR"]
    voice_list = voices if voices is not None else _voices_for(job.target_language)
    detector = detector or FakeLanguageDetector()
    translation = translation or FakeTranslationProvider(langs)
    narration = narration or FakeNarrationProcessor()
    tts = tts or FakeTTSProvider(voice_list, tmp_path / "tts-scratch")
    audio = audio or FakeAudioProcessor()
    jobs = jobs or InMemoryJobStore()
    kwargs: dict[str, object] = {
        "translation": translation,
        "tts": tts,
        "narration": narration,
        "detector": detector,
        "audio": audio,
        "jobs": jobs,
        "retry_policy": retry_policy or RetryPolicy(max_attempts=3, backoff_seconds=1.0),
        "sleep": sleep if sleep is not None else _RecordingSleep(),
    }
    if max_chars is not None:
        kwargs["max_chars"] = max_chars
    orchestrator = PipelineOrchestrator(**kwargs)  # type: ignore[arg-type]
    return orchestrator, workspace, translation, narration, tts, detector, audio, jobs


def _run(
    orchestrator: PipelineOrchestrator,
    job: Job,
    workspace: Path,
    text: str = THREE_SENTENCES,
) -> Job:
    return asyncio.run(orchestrator.run(job, text, workspace=workspace))


def _seed_completed_chunks(workspace: Path, chunks: list, count: int, *, ext: str = "mp3") -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    store = CheckpointStore(workspace)
    for chunk in chunks[:count]:
        translated = workspace / "chunks" / f"{chunk.id}.translated.txt"
        translated.parent.mkdir(parents=True, exist_ok=True)
        translated.write_text(f"seed-translated-{chunk.id}", encoding="utf-8")
        store.record(chunk.id, STAGE_TRANSLATED, translated)
        narrated = workspace / "chunks" / f"{chunk.id}.narrated.txt"
        narrated.write_text(f"seed-narrated-{chunk.id}", encoding="utf-8")
        store.record(chunk.id, STAGE_NARRATED, narrated)
        raw = workspace / "audio" / f"{chunk.id}.{ext}"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"SEEDAUDIO")
        store.record(chunk.id, STAGE_TTS, raw)
        normalized = workspace / "audio" / f"{chunk.id}.normalized.{ext}"
        normalized.write_bytes(b"SEEDAUDIO")
        store.record(chunk.id, STAGE_NORMALIZED, normalized)


def test_three_sentence_fixture_completes(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, _, jobs = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace)
    output = workspace / "output.mp3"
    assert result.status is JobStatus.COMPLETED
    assert result.chunk_total >= 1
    assert result.chunk_current == result.chunk_total
    assert output.is_file() and output.stat().st_size > 0
    assert jobs.jobs[job.id] == result


def test_fake_translator_threads_target_language(tmp_path: Path) -> None:
    outputs: dict[str, str] = {}
    for target in ("en-US", "ko-KR"):
        job = _job(id=f"22222222-2222-2222-2222-22222222222{target[-1]}", target_language=target)
        translation = FakeTranslationProvider(["en-US", "ko-KR"])
        orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
            tmp_path,
            job=job,
            translation=translation,
            voices=_voices_for(target),
        )
        result = _run(orchestrator, job, workspace)
        assert result.status is JobStatus.COMPLETED
        translated = (workspace / "chunks" / "chunk-001.translated.txt").read_text(encoding="utf-8")
        assert f"[{target}]" in translated
        assert translation.calls
        assert all(call[2] == target for call in translation.calls)
        outputs[target] = translated
    assert outputs["en-US"] != outputs["ko-KR"]


def test_pipeline_modules_have_no_language_pair_literals() -> None:
    for path in PIPELINE_DIR.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for literal in LANGUAGE_LITERALS:
            assert literal not in source, f"{literal} found in {path.name}"


def test_explicit_source_skips_detector(tmp_path: Path) -> None:
    job = _job(source_language="en-US")
    detector = FakeLanguageDetector(language_code="ko-KR")
    orchestrator, workspace, translation, _, _, detector, _, _ = _orchestrator(
        tmp_path, job=job, detector=detector
    )
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    assert detector.calls == []
    assert translation.calls
    assert all(call[1] == "en-US" for call in translation.calls)


def test_auto_source_uses_detector(tmp_path: Path) -> None:
    job = _job(source_language=AUTO_SOURCE_LANGUAGE)
    detector = FakeLanguageDetector(language_code="en-US")
    orchestrator, workspace, translation, _, _, detector, _, _ = _orchestrator(
        tmp_path, job=job, detector=detector
    )
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    assert detector.calls == [THREE_SENTENCES]
    assert result.source_language == "en-US"
    assert translation.calls
    assert all(call[1] == "en-US" for call in translation.calls)


def test_narration_is_invoked(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, narration, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    assert isinstance(narration, FakeNarrationProcessor)
    assert narration.calls
    assert all(call[1] == job.target_language for call in narration.calls)


def test_conservative_narration_changes_structure_not_raw_translation(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, narration=ConservativeNarrationProcessor()
    )
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    translated = (workspace / "chunks" / "chunk-001.translated.txt").read_text(encoding="utf-8")
    narrated = (workspace / "chunks" / "chunk-001.narrated.txt").read_text(encoding="utf-8")
    assert narrated != translated
    assert "\n\n" in narrated


def test_multi_chunk_writes_per_chunk_audio_then_merge(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, tts, _, audio, _ = _orchestrator(tmp_path, job=job, max_chars=20)
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    assert result.chunk_total >= 2
    for index in range(1, result.chunk_total + 1):
        chunk_id = f"chunk-{index:03d}"
        assert (workspace / "audio" / f"{chunk_id}.mp3").is_file()
        assert (workspace / "audio" / f"{chunk_id}.normalized.mp3").is_file()
    assert len(tts.calls) == result.chunk_total
    assert len(audio.merge_calls) == 1
    assert len(audio.merge_calls[0]) == result.chunk_total
    assert (workspace / "output.mp3").is_file()


def test_checkpoint_skips_completed_translate_chunk(tmp_path: Path) -> None:
    job = _job()
    chunks = chunk_text(THREE_SENTENCES, max_chars=20)
    assert len(chunks) >= 2
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, max_chars=20
    )
    workspace.mkdir(parents=True)
    translated = workspace / "chunks" / f"{chunks[0].id}.translated.txt"
    translated.parent.mkdir(parents=True)
    translated.write_text("already-translated", encoding="utf-8")
    CheckpointStore(workspace).record(chunks[0].id, STAGE_TRANSLATED, translated)

    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    translated_texts = [call[0] for call in translation.calls]
    assert chunks[0].text not in translated_texts
    assert chunks[1].text in translated_texts
    assert translated.read_text(encoding="utf-8") == "already-translated"


def test_checkpoint_does_not_skip_empty_translate_artifact(tmp_path: Path) -> None:
    job = _job()
    chunks = chunk_text(THREE_SENTENCES, max_chars=20)
    assert len(chunks) >= 2
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, max_chars=20
    )
    workspace.mkdir(parents=True)
    translated = workspace / "chunks" / f"{chunks[0].id}.translated.txt"
    translated.parent.mkdir(parents=True)
    translated.write_bytes(b"")
    CheckpointStore(workspace).record(chunks[0].id, STAGE_TRANSLATED, translated)
    assert translated.stat().st_size == 0

    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    translated_texts = [call[0] for call in translation.calls]
    assert chunks[0].text in translated_texts
    assert chunks[1].text in translated_texts
    assert translated.stat().st_size > 0
    assert f"[{job.target_language}]" in translated.read_text(encoding="utf-8")


def test_legal_status_walk_on_success(tmp_path: Path) -> None:
    job = _job()
    store = RecordingJobStore()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job, jobs=store)
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    ordered: list[JobStatus] = []
    for status in store.statuses:
        if not ordered or ordered[-1] is not status:
            ordered.append(status)
    assert ordered == [
        JobStatus.PARSING,
        JobStatus.TRANSLATING,
        JobStatus.PREPARING_TTS,
        JobStatus.GENERATING_AUDIO,
        JobStatus.MERGING,
        JobStatus.COMPLETED,
    ]


def test_translator_failure_exhausts_retries(tmp_path: Path) -> None:
    job = _job()
    sleep = _RecordingSleep()
    translation = FakeTranslationProvider(["en-US", "ko-KR"], error=RuntimeError("boom"))
    orchestrator, workspace, translation, _, tts, _, _, jobs = _orchestrator(
        tmp_path, job=job, translation=translation, sleep=sleep
    )
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.TRANSLATION_FAILED
    assert len(translation.calls) == 3
    assert sleep.delays == [1.0, 2.0]
    assert tts.calls == []
    assert jobs.jobs[job.id].status is JobStatus.FAILED
    assert not (workspace / "output.mp3").exists()


def test_tts_retries_chunk_003_without_resynthesizing_neighbors(tmp_path: Path) -> None:
    job = _job()
    chunks = chunk_text(FOUR_SENTENCES, max_chars=20)
    assert [chunk.id for chunk in chunks[:4]] == [
        "chunk-001",
        "chunk-002",
        "chunk-003",
        "chunk-004",
    ]
    sleep = _RecordingSleep()
    tts = _FlakyByTextTTS(
        _voices_for(job.target_language),
        tmp_path / "tts-scratch",
        fail_on_text=chunks[2].text,
        fail_times=1,
    )
    orchestrator, workspace, _, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=job, tts=tts, max_chars=20, sleep=sleep
    )
    result = _run(orchestrator, job, workspace, text=FOUR_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    assert sleep.delays == [1.0]
    assert (workspace / "output.mp3").is_file()

    def _calls_for(chunk_text_value: str) -> int:
        return sum(1 for call in tts.calls if chunk_text_value in call[0])

    assert _calls_for(chunks[0].text) == 1
    assert _calls_for(chunks[1].text) == 1
    assert _calls_for(chunks[2].text) == 2
    assert _calls_for(chunks[3].text) == 1


def test_tts_exhaust_on_chunk_003_keeps_neighbor_artifacts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    job = _job()
    chunks = chunk_text(FOUR_SENTENCES, max_chars=20)
    assert len(chunks) >= 4
    sleep = _RecordingSleep()
    tts = _FlakyByTextTTS(
        _voices_for(job.target_language),
        tmp_path / "tts-scratch",
        fail_on_text=chunks[2].text,
        fail_times=3,
    )
    orchestrator, workspace, _, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=job, tts=tts, max_chars=20, sleep=sleep
    )
    with caplog.at_level(logging.INFO, logger="app.application.pipeline.orchestrator"):
        result = _run(orchestrator, job, workspace, text=FOUR_SENTENCES)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.TTS_FAILED
    assert sleep.delays == [1.0, 2.0]
    assert sum(1 for call in tts.calls if chunks[2].text in call[0]) == 3
    assert sum(1 for call in tts.calls if chunks[0].text in call[0]) == 1
    assert sum(1 for call in tts.calls if chunks[1].text in call[0]) == 1
    assert sum(1 for call in tts.calls if chunks[3].text in call[0]) == 0
    assert (workspace / "audio" / "chunk-001.mp3").is_file()
    assert (workspace / "audio" / "chunk-002.mp3").is_file()
    assert not (workspace / "output.mp3").exists()
    failed = [record for record in caplog.records if record.getMessage() == "pipeline_failed"]
    assert failed
    assert failed[0].job_id == job.id
    assert failed[0].chunk_id == "chunk-003"


def test_provider_rate_limit_is_retried(tmp_path: Path) -> None:
    job = _job()
    chunks = chunk_text(FOUR_SENTENCES, max_chars=20)
    tts = _FlakyByTextTTS(
        _voices_for(job.target_language),
        tmp_path / "tts-scratch",
        fail_on_text=chunks[2].text,
        fail_times=1,
        error=DomainError(ErrorType.PROVIDER_RATE_LIMIT, "tts rate limited"),
    )
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, tts=tts, max_chars=20
    )
    result = _run(orchestrator, job, workspace, text=FOUR_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    assert (workspace / "output.mp3").is_file()


def test_timeout_is_retried(tmp_path: Path) -> None:
    job = _job()
    chunks = chunk_text(FOUR_SENTENCES, max_chars=20)
    tts = _FlakyByTextTTS(
        _voices_for(job.target_language),
        tmp_path / "tts-scratch",
        fail_on_text=chunks[2].text,
        fail_times=1,
        error=DomainError(ErrorType.TIMEOUT, "tts timed out"),
    )
    orchestrator, workspace, _, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=job, tts=tts, max_chars=20
    )
    result = _run(orchestrator, job, workspace, text=FOUR_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    assert (workspace / "output.mp3").is_file()
    assert sum(1 for call in tts.calls if chunks[2].text in call[0]) == 2


def test_unsupported_language_is_not_retried(tmp_path: Path) -> None:
    job = _job(target_language="ko-KR")
    translation = FakeTranslationProvider(["en-US"])
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, translation=translation, voices=_voices_for("ko-KR")
    )
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.UNSUPPORTED_LANGUAGE
    assert translation.calls == []


def test_retry_logs_job_chunk_and_retry_count(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    job = _job()
    chunks = chunk_text(FOUR_SENTENCES, max_chars=20)
    tts = _FlakyByTextTTS(
        _voices_for(job.target_language),
        tmp_path / "tts-scratch",
        fail_on_text=chunks[2].text,
        fail_times=1,
    )
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, tts=tts, max_chars=20
    )
    with caplog.at_level(logging.INFO, logger="app.application.pipeline.retry"):
        result = _run(orchestrator, job, workspace, text=FOUR_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    records = [record for record in caplog.records if record.getMessage() == "pipeline_chunk_retry"]
    assert records
    record = records[0]
    assert record.job_id == job.id
    assert record.chunk_id == "chunk-003"
    assert record.retry_count == 1
    assert FOUR_SENTENCES not in record.getMessage()
    assert chunks[2].text not in record.getMessage()


def test_unsupported_voice_fails_job(tmp_path: Path) -> None:
    job = _job(voice="missing-voice")
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.UNSUPPORTED_LANGUAGE


def test_empty_voice_list_fails_job(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job, voices=[])
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.UNSUPPORTED_LANGUAGE


def test_empty_text_is_invalid_input(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace, text="   ")
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.INVALID_INPUT


def test_pipeline_modules_do_not_import_vendors() -> None:
    imported: set[str] = set()
    for path in PIPELINE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
    assert imported.isdisjoint(FORBIDDEN_IMPORT_ROOTS)


def test_checkpoint_json_shape_after_success(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    payload = json.loads((workspace / "checkpoint.json").read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert payload
    for entry in payload:
        assert set(entry) == {"chunk_id", "stage", "artifact_path"}
        assert entry["stage"] in {STAGE_TRANSLATED, "narrated", "tts", "normalized"}
        artifact = workspace / entry["artifact_path"]
        assert artifact.is_file() and artifact.stat().st_size > 0


def test_resume_after_chunk_2_of_5_does_not_regenerate_early_chunks(tmp_path: Path) -> None:
    job = _job(status=JobStatus.GENERATING_AUDIO)
    chunks = chunk_text(FIVE_SENTENCES, max_chars=20)
    assert [chunk.id for chunk in chunks] == [
        "chunk-001",
        "chunk-002",
        "chunk-003",
        "chunk-004",
        "chunk-005",
    ]
    orchestrator, workspace, translation, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=job, max_chars=20
    )
    _seed_completed_chunks(workspace, chunks, 2)

    result = _run(orchestrator, job, workspace, text=FIVE_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    assert (workspace / "output.mp3").is_file()

    translated_texts = [call[0] for call in translation.calls]
    assert chunks[0].text not in translated_texts
    assert chunks[1].text not in translated_texts
    assert chunks[2].text in translated_texts
    assert chunks[3].text in translated_texts
    assert chunks[4].text in translated_texts
    assert (workspace / "chunks" / "chunk-001.translated.txt").read_text(
        encoding="utf-8"
    ) == "seed-translated-chunk-001"
    assert (workspace / "audio" / "chunk-001.mp3").read_bytes() == b"SEEDAUDIO"

    def _tts_mentions(chunk_text_value: str) -> int:
        return sum(1 for call in tts.calls if chunk_text_value in call[0])

    assert _tts_mentions(chunks[0].text) == 0
    assert _tts_mentions(chunks[1].text) == 0
    assert _tts_mentions(chunks[2].text) == 1
    assert _tts_mentions(chunks[3].text) == 1
    assert _tts_mentions(chunks[4].text) == 1


def test_resume_always_remerges_existing_output(tmp_path: Path) -> None:
    job = _job(status=JobStatus.GENERATING_AUDIO)
    chunks = chunk_text(FIVE_SENTENCES, max_chars=20)
    orchestrator, workspace, translation, _, tts, _, audio, _ = _orchestrator(
        tmp_path, job=job, max_chars=20
    )
    _seed_completed_chunks(workspace, chunks, len(chunks))
    stale = workspace / "output.mp3"
    stale.write_bytes(b"STALE-MERGE")

    result = _run(orchestrator, job, workspace, text=FIVE_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    assert audio.merge_calls
    assert stale.read_bytes() != b"STALE-MERGE"
    assert stale.read_bytes() == b"SEEDAUDIO" * len(chunks)
    assert translation.calls == []
    assert tts.calls == []


def test_resume_does_not_reverse_status(tmp_path: Path) -> None:
    job = _job(status=JobStatus.GENERATING_AUDIO)
    store = RecordingJobStore()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, jobs=store, max_chars=20
    )
    chunks = chunk_text(FIVE_SENTENCES, max_chars=20)
    _seed_completed_chunks(workspace, chunks, 2)
    result = _run(orchestrator, job, workspace, text=FIVE_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    assert JobStatus.PARSING not in store.statuses
    assert JobStatus.QUEUED not in store.statuses
    ordered: list[JobStatus] = []
    for status in store.statuses:
        if not ordered or ordered[-1] is not status:
            ordered.append(status)
    assert ordered[-2:] == [JobStatus.MERGING, JobStatus.COMPLETED]


def test_run_is_noop_for_completed_and_failed(tmp_path: Path) -> None:
    completed = _job(status=JobStatus.COMPLETED)
    orchestrator, workspace, translation, _, tts, _, _, _ = _orchestrator(tmp_path, job=completed)
    result = _run(orchestrator, completed, workspace)
    assert result.status is JobStatus.COMPLETED
    assert translation.calls == []
    assert tts.calls == []

    failed = _job(status=JobStatus.FAILED, id="22222222-2222-2222-2222-222222222222")
    orchestrator, workspace, translation, _, tts, _, _, _ = _orchestrator(tmp_path, job=failed)
    result = _run(orchestrator, failed, workspace)
    assert result.status is JobStatus.FAILED
    assert translation.calls == []
    assert tts.calls == []
