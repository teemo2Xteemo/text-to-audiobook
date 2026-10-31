from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path

import pytest

from app.application.pipeline.artifact_cache import CacheIdentity, PipelineArtifactCache
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
from app.domain.jobs import Job, JobMode, JobStatus, OutputFormat
from app.domain.languages import AUTO_SOURCE_LANGUAGE
from app.domain.ports import NarrationProcessor
from app.domain.retry import RetryPolicy
from app.infrastructure.artifact_cache_fs import FilesystemArtifactCache
from app.infrastructure.checkpoint_fs import CheckpointFilesystem
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
    artifact_cache: PipelineArtifactCache | None = None,
    chapter_pause_ms: int = 0,
    text_normalizer: object | None = None,
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
        "checkpoint_storage_factory": CheckpointFilesystem,
        "retry_policy": retry_policy or RetryPolicy(max_attempts=3, backoff_seconds=1.0),
        "sleep": sleep if sleep is not None else _RecordingSleep(),
        "chapter_pause_ms": chapter_pause_ms,
        "text_normalizer": text_normalizer,
    }
    if artifact_cache is not None:
        kwargs["artifact_cache"] = artifact_cache
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
    store = CheckpointStore(storage=CheckpointFilesystem(workspace))
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
        job = _job(
            id=f"22222222-2222-2222-2222-22222222222{target[-1]}",
            source_language="de-DE",
            target_language=target,
        )
        translation = FakeTranslationProvider(["de-DE", "en-US", "ko-KR"])
        orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
            tmp_path,
            job=job,
            translation=translation,
            voices=_voices_for(target),
            languages=["de-DE", "en-US", "ko-KR"],
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
    CheckpointStore(storage=CheckpointFilesystem(workspace)).record(
        chunks[0].id, STAGE_TRANSLATED, translated
    )

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
    CheckpointStore(storage=CheckpointFilesystem(workspace)).record(
        chunks[0].id, STAGE_TRANSLATED, translated
    )
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


def test_resume_in_generating_audio_does_not_oscillate_chunk_progress_during_earlier_stages(
    tmp_path: Path,
) -> None:
    chunks = chunk_text(FIVE_SENTENCES, max_chars=20)
    job = _job(status=JobStatus.GENERATING_AUDIO, chunk_current=2, chunk_total=len(chunks))

    class _ProgressStore(InMemoryJobStore):
        def __init__(self) -> None:
            super().__init__()
            self.snapshots: list[tuple[JobStatus, int, int]] = []

        async def save(self, saved_job: Job) -> None:
            self.snapshots.append(
                (saved_job.status, saved_job.chunk_current, saved_job.chunk_total)
            )
            await super().save(saved_job)

    store = _ProgressStore()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, jobs=store, max_chars=20
    )
    _seed_completed_chunks(workspace, chunks, 2)

    result = _run(orchestrator, job, workspace, text=FIVE_SENTENCES)
    assert result.status is JobStatus.COMPLETED

    gen_indices = [
        chunk_cur
        for status, chunk_cur, _ in store.snapshots
        if status is JobStatus.GENERATING_AUDIO
    ]
    assert 0 not in gen_indices
    assert gen_indices == sorted(gen_indices)


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


def _pipeline_cache(tmp_path: Path, store: object | None = None) -> PipelineArtifactCache:
    backend = store if store is not None else FilesystemArtifactCache(tmp_path / "cache")
    return PipelineArtifactCache(
        backend,  # type: ignore[arg-type]
        CacheIdentity(
            translation_provider="fake",
            translation_model="fake",
            tts_provider="fake",
            tts_model="fake",
        ),
    )


class _RecordingCache:
    def __init__(self, inner: FilesystemArtifactCache) -> None:
        self.inner = inner
        self.gets: list[tuple[str, str]] = []
        self.puts: list[tuple[str, str]] = []

    def get(self, operation: str, key: str, destination: Path) -> bool:
        self.gets.append((destination.name.split(".", 1)[0], operation))
        return self.inner.get(operation, key, destination)

    def put(self, operation: str, key: str, source: Path) -> None:
        self.puts.append((operation, key))
        self.inner.put(operation, key, source)


class _FailingPutCache:
    def __init__(self) -> None:
        self.puts = 0

    def get(self, operation: str, key: str, destination: Path) -> bool:
        return False

    def put(self, operation: str, key: str, source: Path) -> None:
        self.puts += 1
        raise OSError("disk full")


def test_second_job_hits_translation_and_tts_cache(tmp_path: Path) -> None:
    cache = _pipeline_cache(tmp_path)
    first = _job()
    first_orch, first_ws, translation, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=first, artifact_cache=cache
    )
    assert _run(first_orch, first, first_ws).status is JobStatus.COMPLETED
    first_translation_calls = len(translation.calls)
    first_tts_calls = len(tts.calls)
    assert first_translation_calls > 0
    assert first_tts_calls > 0

    second = _job(id="22222222-2222-2222-2222-222222222222")
    second_orch, second_ws, translation, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=second, translation=translation, tts=tts, artifact_cache=cache
    )
    result = _run(second_orch, second, second_ws)
    assert result.status is JobStatus.COMPLETED
    assert len(translation.calls) == first_translation_calls
    assert len(tts.calls) == first_tts_calls
    assert (second_ws / "output.mp3").is_file()
    payload = json.loads((second_ws / "checkpoint.json").read_text(encoding="utf-8"))
    stages = {(item["chunk_id"], item["stage"]) for item in payload}
    assert ("chunk-001", STAGE_TRANSLATED) in stages
    assert ("chunk-001", STAGE_TTS) in stages


def test_target_language_change_misses_cache(tmp_path: Path) -> None:
    cache = _pipeline_cache(tmp_path)
    first = _job(target_language="ko-KR")
    first_orch, first_ws, translation, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=first,
        artifact_cache=cache,
        voices=_voices_for("ko-KR"),
        languages=["en-US", "ko-KR", "de-DE"],
    )
    _run(first_orch, first, first_ws)
    after_first_t = len(translation.calls)

    second = _job(id="22222222-2222-2222-2222-222222222222", target_language="de-DE")
    second_orch, second_ws, translation, _, tts_second, _, _, _ = _orchestrator(
        tmp_path,
        job=second,
        translation=translation,
        artifact_cache=cache,
        voices=_voices_for("de-DE"),
        languages=["en-US", "ko-KR", "de-DE"],
    )
    _run(second_orch, second, second_ws)
    assert len(translation.calls) > after_first_t
    assert tts_second.calls


def test_speed_change_misses_tts_but_hits_translation(tmp_path: Path) -> None:
    cache = _pipeline_cache(tmp_path)
    first = _job(speed=1.0)
    first_orch, first_ws, translation, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=first, artifact_cache=cache
    )
    _run(first_orch, first, first_ws)
    after_first_t = len(translation.calls)
    after_first_tts = len(tts.calls)

    second = _job(id="22222222-2222-2222-2222-222222222222", speed=1.25)
    second_orch, _, translation, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=second, translation=translation, tts=tts, artifact_cache=cache
    )
    _run(second_orch, second, tmp_path / "jobs" / second.id)
    assert len(translation.calls) == after_first_t
    assert len(tts.calls) > after_first_tts


def test_voice_change_misses_tts_cache(tmp_path: Path) -> None:
    cache = _pipeline_cache(tmp_path)
    voices = [
        Voice(id="voice-a", language="ko-KR", label="A"),
        Voice(id="voice-b", language="ko-KR", label="B"),
    ]
    first = _job(voice="voice-a")
    first_orch, first_ws, _, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=first, artifact_cache=cache, voices=voices
    )
    _run(first_orch, first, first_ws)
    after_first = len(tts.calls)

    second = _job(id="22222222-2222-2222-2222-222222222222", voice="voice-b")
    second_orch, _, _, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=second, tts=tts, artifact_cache=cache, voices=voices
    )
    _run(second_orch, second, tmp_path / "jobs" / second.id)
    assert len(tts.calls) > after_first


def test_checkpoint_skip_does_not_lookup_cache(tmp_path: Path) -> None:
    recorder = _RecordingCache(FilesystemArtifactCache(tmp_path / "cache"))
    cache = _pipeline_cache(tmp_path, store=recorder)
    job = _job(status=JobStatus.GENERATING_AUDIO)
    chunks = chunk_text(FIVE_SENTENCES, max_chars=20)
    orchestrator, workspace, translation, _, tts, _, _, _ = _orchestrator(
        tmp_path, job=job, max_chars=20, artifact_cache=cache
    )
    seeded = chunks[:2]
    remaining = chunks[2:]
    assert remaining
    _seed_completed_chunks(workspace, chunks, len(seeded))
    _run(orchestrator, job, workspace, text=FIVE_SENTENCES)
    translation_texts = [call[0] for call in translation.calls]
    tts_texts = [call[0] for call in tts.calls]
    for chunk in seeded:
        assert chunk.text not in translation_texts
        assert f"seed-narrated-{chunk.id}" not in tts_texts
    assert set(recorder.gets) == {
        (chunk.id, operation) for chunk in remaining for operation in ("translation", "tts")
    }


def test_cache_put_failure_does_not_fail_job(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = _FailingPutCache()
    cache = _pipeline_cache(tmp_path, store=store)
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, artifact_cache=cache
    )
    with caplog.at_level(logging.WARNING, logger="app.application.pipeline.artifact_cache"):
        result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    assert store.puts > 0
    assert (workspace / "chunks" / "chunk-001.translated.txt").stat().st_size > 0
    assert (workspace / "audio" / "chunk-001.mp3").stat().st_size > 0
    assert (workspace / "output.mp3").is_file()
    failed_puts = [record for record in caplog.records if record.getMessage() == "cache_put_failed"]
    assert failed_puts
    assert {record.operation for record in failed_puts} >= {"translation", "tts"}
    assert all(record.job_id == job.id for record in failed_puts)


def test_empty_cache_blob_is_a_miss(tmp_path: Path) -> None:
    cache = _pipeline_cache(tmp_path)
    first = _job()
    first_orch, first_ws, translation, _, _, _, _, _ = _orchestrator(
        tmp_path, job=first, artifact_cache=cache
    )
    _run(first_orch, first, first_ws)
    blobs = list((tmp_path / "cache" / "translation").iterdir())
    assert blobs
    blobs[0].write_bytes(b"")
    after_first = len(translation.calls)

    second = _job(id="22222222-2222-2222-2222-222222222222")
    second_orch, _, translation, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=second,
        translation=translation,
        artifact_cache=cache,
    )
    _run(second_orch, second, tmp_path / "jobs" / second.id)
    assert len(translation.calls) > after_first


def test_tts_scratch_files_are_moved_not_left_behind(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job, max_chars=20)
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.COMPLETED
    scratch_dir = tmp_path / "tts-scratch"
    assert list(scratch_dir.glob("*")) == []


def test_checkpoint_store_caches_entries_in_memory_and_preserves_state_on_save_failure(
    tmp_path: Path,
) -> None:
    from app.application.pipeline import checkpoint as checkpoint_mod
    from app.infrastructure.checkpoint_fs import CheckpointFilesystem

    assert not hasattr(checkpoint_mod, "_WorkspaceCheckpointStorage")

    workspace = tmp_path / "job"
    workspace.mkdir()
    real_fs = CheckpointFilesystem(workspace)
    load_calls = 0
    fail_save = False

    class _CountingStorage:
        def load_raw(self) -> list[dict[str, str]]:
            nonlocal load_calls
            load_calls += 1
            return real_fs.load_raw()

        def save_raw(self, entries: list[dict[str, str]]) -> None:
            if fail_save:
                raise DomainError(ErrorType.STORAGE_FAILED, "disk full")
            real_fs.save_raw(entries)

        def relative(self, artifact: Path) -> str:
            return real_fs.relative(artifact)

        def resolve(self, artifact_path: str) -> Path:
            return real_fs.resolve(artifact_path)

    store = CheckpointStore(storage=_CountingStorage())
    a1 = workspace / "chunks" / "chunk-001.translated.txt"
    a1.parent.mkdir(parents=True)
    a1.write_text("one", encoding="utf-8")
    a2 = workspace / "chunks" / "chunk-002.translated.txt"
    a2.write_text("two", encoding="utf-8")

    assert not store.is_complete("chunk-001", STAGE_TRANSLATED)
    store.record("chunk-001", STAGE_TRANSLATED, a1)
    assert store.is_complete("chunk-001", STAGE_TRANSLATED)
    assert not store.is_complete("chunk-002", STAGE_TRANSLATED)
    assert load_calls == 1

    fail_save = True
    with pytest.raises(DomainError):
        store.record("chunk-002", STAGE_TRANSLATED, a2)
    assert not store.is_complete("chunk-002", STAGE_TRANSLATED)
    assert load_calls == 1


def test_pipeline_strips_srt_timecodes_and_tags_before_translation(tmp_path: Path) -> None:
    srt_text = (
        "1\n"
        "00:01:02,000 --> 00:01:05,500\n"
        "<i>Alpha is first.</i>\n\n"
        "2\n"
        "00:01:06,000 --> 00:01:09,000\n"
        "Bravo is second.\n"
    )
    job = _job()
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace, text=srt_text)
    assert result.status is JobStatus.COMPLETED
    translated_inputs = " ".join(call[0] for call in translation.calls)
    assert "00:01:02" not in translated_inputs
    assert "-->" not in translated_inputs
    assert "<i>" not in translated_inputs
    assert "Alpha is first." in translated_inputs
    assert "Bravo is second." in translated_inputs


def test_pipeline_rejects_srt_with_only_timecodes_and_no_dialogue(tmp_path: Path) -> None:
    srt_only_meta = "1\n00:00:01,000 --> 00:00:02,000\n\n2\n00:00:03,000 --> 00:00:04,000\n"
    job = _job()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace, text=srt_only_meta)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.INVALID_INPUT


def test_webvtt_with_dot_timecodes_is_stripped() -> None:
    from app.application.pipeline.parse import strip_srt

    vtt = (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.500\nHello <i>there</i>.\n\n"
        "00:00:03.000 --> 00:00:04.000\nSecond line.\n"
    )
    assert strip_srt(vtt) == "Hello there. Second line."


def test_strip_srt_strips_ass_tags_and_sound_effects() -> None:
    from app.application.pipeline.parse import strip_srt

    srt = (
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "{\\an8}Hello {\\b1}brave{\\b0} world!\n\n"
        "2\n"
        "00:00:04,000 --> 00:00:06,000\n"
        "[Music]\n\n"
        "3\n"
        "00:00:07,000 --> 00:00:09,000\n"
        "♪ Uplifting intro ♪\n\n"
        "4\n"
        "00:00:10,000 --> 00:00:12,000\n"
        "Welcome to the show. (applause)\n"
    )
    result = strip_srt(srt)
    assert result == "Hello brave world!\n\nWelcome to the show."


def _srt(*cues: str) -> str:
    return "\n\n".join(
        f"{idx}\n00:{idx // 60:02d}:{idx % 60:02d},000 --> 00:{idx // 60:02d}:{idx % 60:02d},900\n"
        f"{cue}"
        for idx, cue in enumerate(cues, start=1)
    )


def test_strip_srt_collapses_rolling_captions() -> None:
    from app.application.pipeline.parse import strip_srt

    srt = _srt("Hello there", "Hello there my friend", "my friend how are you")
    assert strip_srt(srt) == "Hello there my friend how are you"


@pytest.mark.parametrize("max_chars", [1200, 500])
def test_rolling_unpunctuated_captions_chunk_on_word_boundaries(max_chars: int) -> None:
    from app.application.pipeline.parse import strip_srt

    words = [f"tu{i}" for i in range(302)]
    cues = [" ".join(words[i : i + 3]) for i in range(300)]
    stripped = strip_srt(_srt(*cues))
    assert stripped.split() == words
    chunks = chunk_text(stripped, max_chars=max_chars)
    assert len(chunks) > 1
    assert [w for chunk in chunks for w in chunk.text.split()] == words


def test_strip_srt_keeps_cues_sharing_a_single_word() -> None:
    from app.application.pipeline.parse import strip_srt

    srt = _srt("I saw you.", "you never listen.")
    assert strip_srt(srt) == "I saw you. you never listen."
    assert strip_srt(_srt("I saw you.", "you.")) == "I saw you. you."
    assert strip_srt(_srt("Hello there my friend", "my friend")) == "Hello there my friend"


def test_strip_srt_drops_identical_consecutive_cues() -> None:
    # Deliberate: back-to-back identical cues are treated as a rendering repeat,
    # so a genuinely repeated short line ("No." / "No.") is narrated once.
    from app.application.pipeline.parse import strip_srt

    srt = _srt("No.", "No.", "Fine.", "No.")
    assert strip_srt(srt) == "No. Fine. No."


def test_strip_srt_breaks_paragraph_only_after_long_gap_and_sentence_end() -> None:
    from app.application.pipeline.parse import strip_srt

    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\nShe opened the door\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nand stepped inside.\n\n"
        "3\n00:00:06,500 --> 00:00:07,000\nIt was dark.\n\n"
        "4\n00:00:12,000 --> 00:00:13,000\nMorning came.\n"
    )
    # 3 s gap after an unfinished sentence still joins; 5 s gap after "dark." breaks.
    assert strip_srt(srt) == (
        "She opened the door and stepped inside. It was dark.\n\nMorning came."
    )
    assert strip_srt(srt, paragraph_gap_ms=10_000) == (
        "She opened the door and stepped inside. It was dark. Morning came."
    )


def test_plain_txt_with_embedded_timecode_and_chapter_numbers_is_not_stripped(
    tmp_path: Path,
) -> None:
    del tmp_path
    from app.application.pipeline.parse import parse_source

    story = "1\nChapter One\n\n00:00:01,000 --> 00:00:02,000\nLine two of the paragraph."
    normalized, resolved = asyncio.run(parse_source(story, "en-US", FakeLanguageDetector("en-US")))
    assert resolved == "en-US"
    assert normalized == story


def test_pipeline_resets_recovery_attempts_when_chunk_progress_advances(tmp_path: Path) -> None:
    from dataclasses import replace

    job = replace(_job(), recovery_attempts=2)
    orchestrator, workspace, _, _, _, _, _, store = _orchestrator(tmp_path, job=job, max_chars=20)
    result = _run(orchestrator, job, workspace, text=FOUR_SENTENCES)
    assert result.status is JobStatus.COMPLETED
    assert result.recovery_attempts == 0
    saved = asyncio.run(store.get(job.id))
    assert saved is not None and saved.recovery_attempts == 0


def test_narrate_mode_executes_zero_translation_calls(tmp_path: Path) -> None:
    job = _job(
        id="test-narrate-1",
        source_language="vi-VN",
        target_language="vi-VN",
        mode=JobMode.NARRATE,
        voice="voice-vi-VN",
    )
    orchestrator, workspace, translation, _, _, _, _, jobs = _orchestrator(
        tmp_path,
        job=job,
        languages=["vi-VN"],
        voices=_voices_for("vi-VN"),
    )
    result = _run(orchestrator, job, workspace, text="Đây là văn bản thử nghiệm không cần dịch.")

    assert len(translation.calls) == 0
    assert result.status is JobStatus.COMPLETED
    saved = asyncio.run(jobs.get(job.id))
    assert saved is not None and saved.status is JobStatus.COMPLETED


def test_narrate_mode_splits_chapters_and_preserves_titles(tmp_path: Path) -> None:
    job = _job(
        id="test-narrate-chapters-1",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
    )
    orchestrator, workspace, translation, _, _, tts, _, jobs = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=500,
    )
    story = (
        "Chapter 1: The Beginning\n\n"
        "It started on a cold winter morning.\n\n"
        "Chapter 2: The Journey\n\n"
        "They traveled across the mountains."
    )
    result = _run(orchestrator, job, workspace, text=story)
    assert result.status is JobStatus.COMPLETED
    assert result.chunk_total == 2
    chunk_1_text = (workspace / "chunks" / "chunk-001.translated.txt").read_text(encoding="utf-8")
    chunk_2_text = (workspace / "chunks" / "chunk-002.translated.txt").read_text(encoding="utf-8")
    assert "Chapter 1: The Beginning" in chunk_1_text
    assert "It started on a cold winter morning." in chunk_1_text
    assert "Chapter 2: The Journey" in chunk_2_text
    assert "They traveled across the mountains." in chunk_2_text


@pytest.mark.parametrize("pause_ms", [0, 1500])
def test_narrate_mode_inserts_pause_between_chapters_only(tmp_path: Path, pause_ms: int) -> None:
    job = _job(
        id="test-narrate-pause-1",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
    )
    orchestrator, workspace, _, _, _, _, audio, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=100,
        chapter_pause_ms=pause_ms,
    )
    story = (
        "Chapter 1: Dawn\n\n"
        + "The sun rose slowly over the quiet hills. " * 4
        + "\n\nChapter 2: Dusk\n\nThe sun set."
    )
    result = _run(orchestrator, job, workspace, text=story)

    assert result.status is JobStatus.COMPLETED
    merged = [path.name for path in audio.merge_calls[-1]]
    pause = "chapter-pause.mp3"
    if pause_ms == 0:
        assert pause not in merged
        assert audio.silence_calls == []
        return
    # Chapter 1 spans several chunks; the single pause sits only at the chapter boundary.
    assert merged.count(pause) == 1
    boundary = merged.index(pause)
    assert boundary > 1
    assert merged[boundary + 1] == f"chunk-{boundary + 1:03d}.normalized.mp3"
    assert audio.silence_calls == [(pause_ms, workspace / "audio" / pause)]


def test_translate_mode_never_inserts_chapter_pauses(tmp_path: Path) -> None:
    job = _job()
    orchestrator, workspace, _, _, _, _, audio, _ = _orchestrator(
        tmp_path, job=job, chapter_pause_ms=1500
    )
    story = "Chapter 1: A\n\nOne.\n\nChapter 2: B\n\nTwo."
    result = _run(orchestrator, job, workspace, text=story)

    assert result.status is JobStatus.COMPLETED
    assert audio.silence_calls == []


def test_narrate_mode_empty_readable_text_raises_invalid_input(tmp_path: Path) -> None:
    job = _job(
        id="test-narrate-empty-1",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
    )
    orchestrator, workspace, _, _, _, _, _, jobs = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
    )
    result = _run(orchestrator, job, workspace, text="***\n---\n===")
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.INVALID_INPUT
    assert "No readable text found" in (result.message or "")


def test_translate_same_language_bypasses_translation(tmp_path: Path) -> None:
    job = _job(
        id="test-trans-same-1",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.TRANSLATE,
        voice="voice-en-US",
    )
    orchestrator, workspace, translation, _, _, _, _, jobs = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
    )
    result = _run(orchestrator, job, workspace, text="English text with same source and target.")

    assert len(translation.calls) == 0
    assert result.status is JobStatus.COMPLETED
    saved = asyncio.run(jobs.get(job.id))
    assert saved is not None and saved.status is JobStatus.COMPLETED


def test_unknown_job_mode_fails_pipeline(tmp_path: Path) -> None:
    job = _job(
        id="test-unknown-mode-1",
        mode=JobMode.UNKNOWN,
    )
    orchestrator, workspace, _, _, _, _, _, jobs = _orchestrator(tmp_path, job=job)
    result = _run(orchestrator, job, workspace)

    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.INVALID_INPUT
    assert "Unknown job mode" in (result.message or "")
    saved = asyncio.run(jobs.get(job.id))
    assert saved is not None and saved.status is JobStatus.FAILED


_TWO_CUES_WITH_GAP = (
    "1\n00:00:01,000 --> 00:00:02,000\nFirst line.\n\n"
    "2\n00:00:10,000 --> 00:00:11,000\nSecond line.\n"
)


def test_narrate_srt_aligned_follows_cue_timeline_without_translation(tmp_path: Path) -> None:
    from app.domain.jobs import SrtTiming

    job = _job(
        id="test-narrate-srt-aligned",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        srt_timing=SrtTiming.ALIGNED,
    )
    orchestrator, workspace, translation, _, _, _, audio, _ = _orchestrator(
        tmp_path, job=job, voices=_voices_for("en-US")
    )
    result = _run(orchestrator, job, workspace, text=_TWO_CUES_WITH_GAP)

    assert result.status is JobStatus.COMPLETED
    assert translation.calls == []
    # Aligned timing keeps the cue gaps as silence and writes timed subtitles.
    assert audio.silence_calls
    assert (workspace / "output.srt").read_text(encoding="utf-8").count("-->") == 2


def test_narrate_srt_continuous_is_default_and_has_no_timeline(tmp_path: Path) -> None:
    job = _job(
        id="test-narrate-srt-continuous",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
    )
    orchestrator, workspace, _, _, _, _, audio, _ = _orchestrator(
        tmp_path, job=job, voices=_voices_for("en-US")
    )
    result = _run(orchestrator, job, workspace, text=_TWO_CUES_WITH_GAP)

    assert result.status is JobStatus.COMPLETED
    assert audio.silence_calls == []
    assert not (workspace / "output.srt").exists()


def test_srt_with_narrate_mode_produces_audio_successfully(tmp_path: Path) -> None:
    job = _job(
        id="test-narrate-srt-1",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
    )
    orchestrator, workspace, _, _, _, _, _, jobs = _orchestrator(tmp_path, job=job)
    srt_text = "1\n00:00:01,000 --> 00:00:04,000\nHello subtitle\n"
    result = _run(orchestrator, job, workspace, text=srt_text)

    assert result.status is JobStatus.COMPLETED
    assert result.error_type is None
    saved = asyncio.run(jobs.get(job.id))
    assert saved is not None and saved.status is JobStatus.COMPLETED


class _SpellingNormalizer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def normalize(self, text: str, language: str) -> str:
        self.calls.append((text, language))
        return text.replace("20", "twenty")


def _normalize_job(job_id: str, *, normalize_text: bool = True) -> Job:
    return _job(
        id=job_id,
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
        normalize_text=normalize_text,
    )


def test_normalize_text_feeds_spoken_form_to_narration_and_is_cached(tmp_path: Path) -> None:
    normalizer = _SpellingNormalizer()
    cache = _pipeline_cache(tmp_path)
    for job_id in ("normalize-a", "normalize-b"):
        job = _normalize_job(job_id)
        orchestrator, workspace, translation, narration, _, _, _, _ = _orchestrator(
            tmp_path,
            job=job,
            languages=["en-US"],
            voices=_voices_for("en-US"),
            artifact_cache=cache,
            text_normalizer=normalizer,
        )
        result = _run(orchestrator, job, workspace, text="She was 20 years old.")

        assert result.status is JobStatus.COMPLETED
        assert translation.calls == []
        assert narration.calls == [("She was twenty years old.", "en-US")]  # type: ignore[attr-defined]
    # The second job reuses the cached normalization instead of calling the LLM again.
    assert normalizer.calls == [("She was 20 years old.", "en-US")]


def test_normalizer_is_skipped_unless_the_job_opted_in(tmp_path: Path) -> None:
    normalizer = _SpellingNormalizer()
    job = _normalize_job("normalize-off", normalize_text=False)
    orchestrator, workspace, _, narration, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        text_normalizer=normalizer,
    )
    result = _run(orchestrator, job, workspace, text="She was 20 years old.")

    assert result.status is JobStatus.COMPLETED
    assert normalizer.calls == []
    assert narration.calls == [("She was 20 years old.", "en-US")]  # type: ignore[attr-defined]


def test_normalize_text_without_normalizer_fails_with_configuration_error(
    tmp_path: Path,
) -> None:
    job = _normalize_job("normalize-missing")
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, languages=["en-US"], voices=_voices_for("en-US")
    )
    result = _run(orchestrator, job, workspace, text="She was 20 years old.")

    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.PROVIDER_CONFIGURATION_ERROR


def test_narrate_srt_aligned_does_not_voice_sound_effects(tmp_path: Path) -> None:
    from app.domain.jobs import SrtTiming

    job = _job(
        id="test-narrate-srt-aligned-sfx",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        srt_timing=SrtTiming.ALIGNED,
    )
    orchestrator, workspace, _, narration, _, _, _, _ = _orchestrator(
        tmp_path, job=job, voices=_voices_for("en-US")
    )
    srt_text = (
        "1\n00:00:01,000 --> 00:00:02,000\n[Music]\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nFirst line.\n"
    )
    result = _run(orchestrator, job, workspace, text=srt_text)

    assert result.status is JobStatus.COMPLETED
    narrated = " ".join(text for text, _ in narration.calls)  # type: ignore[attr-defined]
    assert "Music" not in narrated
    assert "First line." in narrated


def test_volume_heading_gets_no_pause_of_its_own(tmp_path: Path) -> None:
    job = _job(
        id="test-narrate-volume-pause",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
    )
    orchestrator, workspace, _, _, _, _, audio, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=100,
        chapter_pause_ms=1500,
    )
    story = "Book 1\n\nChapter 1: Dawn\n\nThe sun rose.\n\nChapter 2: Dusk\n\nThe sun set."
    result = _run(orchestrator, job, workspace, text=story)

    assert result.status is JobStatus.COMPLETED
    merged = [path.name for path in audio.merge_calls[-1]]
    chapter_two = next(
        path.name.removesuffix(".translated.txt")
        for path in sorted((workspace / "chunks").glob("*.translated.txt"))
        if "Chapter 2" in path.read_text(encoding="utf-8")
    )
    # Only the Chapter 1 -> Chapter 2 boundary pauses; "Book 1" flows into Chapter 1.
    assert merged.count("chapter-pause.mp3") == 1
    pause_at = merged.index("chapter-pause.mp3")
    assert merged[pause_at + 1] == f"{chapter_two}.normalized.mp3"


def test_strip_srt_breaks_unpunctuated_captions_on_long_silence() -> None:
    from app.application.pipeline.parse import strip_srt

    srt = (
        "1\n00:00:01,000 --> 00:00:02,000\nso we went down\n\n"
        "2\n00:00:05,000 --> 00:00:06,000\nto the river\n\n"
        "3\n00:00:11,000 --> 00:00:12,000\nthe next day it rained\n"
    )
    # 3 s gap without punctuation still joins; 5 s (> 2 x 2 s) breaks regardless.
    assert strip_srt(srt) == "so we went down to the river\n\nthe next day it rained"


class _FixedNormalizer:
    def __init__(self, output: str) -> None:
        self.output = output

    async def normalize(self, text: str, language: str) -> str:
        return self.output


@pytest.mark.parametrize(
    "bad_output",
    [
        "She was twenty.",  # truncated: second line dropped
        "Here is the rewritten text:\nShe was twenty years old.\nHe was thirty.",  # preamble
        "她二十岁。\n他三十岁。",  # answered in another script
    ],
)
def test_rejected_normalization_narrates_original_and_is_not_cached(
    tmp_path: Path, bad_output: str
) -> None:
    original = "She was 20 years old.\nHe was 30."
    job = _normalize_job("normalize-rejected")
    orchestrator, workspace, _, narration, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        artifact_cache=_pipeline_cache(tmp_path),
        text_normalizer=_FixedNormalizer(bad_output),
    )
    result = _run(orchestrator, job, workspace, text=original)

    assert result.status is JobStatus.COMPLETED
    assert narration.calls == [(original, "en-US")]  # type: ignore[attr-defined]
    assert not any((tmp_path / "cache" / "normalization").glob("*"))


class _ConcurrencyNormalizer:
    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0

    async def normalize(self, text: str, language: str) -> str:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.01)
        self.in_flight -= 1
        return text.replace("20", "twenty")


def test_normalization_runs_with_bounded_concurrency(tmp_path: Path) -> None:
    normalizer = _ConcurrencyNormalizer()
    job = _normalize_job("normalize-concurrent")
    orchestrator, workspace, _, narration, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=100,
        text_normalizer=normalizer,
    )
    orchestrator._translation_concurrency = 2
    story = "\n\n".join(
        f"Paragraph {i} says she was 20 at the time of the story." for i in range(6)
    )
    result = _run(orchestrator, job, workspace, text=story)

    assert result.status is JobStatus.COMPLETED
    assert normalizer.max_in_flight == 2
    narrated = " ".join(text for text, _ in narration.calls)  # type: ignore[attr-defined]
    assert " 20 " not in narrated
    assert narrated.count("twenty") == 6


def test_narrate_srt_never_translates_even_if_tags_differ(tmp_path: Path) -> None:
    from app.domain.jobs import SrtTiming

    job = _job(
        id="test-narrate-srt-tag-drift",
        source_language="en-US",
        target_language="en-GB",
        mode=JobMode.NARRATE,
        srt_timing=SrtTiming.ALIGNED,
    )
    orchestrator, workspace, translation, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, languages=["en-US", "en-GB"], voices=_voices_for("en-GB")
    )
    result = _run(orchestrator, job, workspace, text=_TWO_CUES_WITH_GAP)

    assert result.status is JobStatus.COMPLETED
    assert translation.calls == []


def test_unfaithful_cached_normalization_is_recomputed(tmp_path: Path) -> None:
    original = "She was 20 years old."
    cache = _pipeline_cache(tmp_path)
    poisoned = tmp_path / "poisoned.txt"
    poisoned.write_text("Here is the text: She was twenty years old.", encoding="utf-8")
    cache.store_normalization(
        text=original, language="en-US", source=poisoned, job_id="seed", chunk_id="seed"
    )
    normalizer = _SpellingNormalizer()
    job = _normalize_job("normalize-poisoned-cache")
    orchestrator, workspace, _, narration, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        artifact_cache=cache,
        text_normalizer=normalizer,
    )
    result = _run(orchestrator, job, workspace, text=original)

    assert result.status is JobStatus.COMPLETED
    assert normalizer.calls == [(original, "en-US")]
    assert narration.calls == [("She was twenty years old.", "en-US")]  # type: ignore[attr-defined]


class _FailingOnNormalizer(_SpellingNormalizer):
    def __init__(self, marker: str) -> None:
        super().__init__()
        self.marker = marker

    async def normalize(self, text: str, language: str) -> str:
        if self.marker in text:
            raise DomainError(ErrorType.INVALID_INPUT, "normalizer refused")
        return await super().normalize(text, language)


def test_resume_reuses_normalized_chunks_on_disk_without_cache(tmp_path: Path) -> None:
    from dataclasses import replace as dc_replace

    line = "says she was 20 at the time of the story."
    story = "\n\n".join(f"Paragraph {i} {line}" for i in range(5))
    job = _normalize_job("normalize-resume")
    failing = _FailingOnNormalizer("Paragraph 2 ")
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=100,
        text_normalizer=failing,
    )
    failed = _run(orchestrator, job, workspace, text=story)
    assert failed.status is JobStatus.FAILED
    accepted = {text.split(" ")[1] for text, _ in failing.calls}
    assert "2" not in accepted and {"0", "1"} <= accepted

    retry = dc_replace(failed, status=JobStatus.QUEUED, error_type=None, message=None)
    resumed_normalizer = _SpellingNormalizer()
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path,
        job=retry,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=100,
        text_normalizer=resumed_normalizer,
    )
    result = _run(orchestrator, retry, workspace, text=story)

    assert result.status is JobStatus.COMPLETED
    # Chunks accepted before the failure are read back from disk, not sent to the LLM again.
    retried = {text.split(" ")[1] for text, _ in resumed_normalizer.calls}
    assert retried == {"0", "1", "2", "3", "4"} - accepted


def test_stale_normalized_file_from_other_text_is_not_reused(tmp_path: Path) -> None:
    job = _normalize_job("normalize-stale-file")
    workspace = tmp_path / "jobs" / job.id
    stale = workspace / "chunks" / "chunk-001.normalized.txt"
    stale.parent.mkdir(parents=True, exist_ok=True)
    # Same chunk id and opening word, but made from a different passage (pre-re-chunking).
    stale.write_text("She stood still in the yard and counted three disciples.", encoding="utf-8")
    normalizer = _SpellingNormalizer()
    orchestrator, workspace, _, narration, _, _, _, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        text_normalizer=normalizer,
    )
    original = "She walked into the great hall and saw 20 elders."
    result = _run(orchestrator, job, workspace, text=original)

    assert result.status is JobStatus.COMPLETED
    assert normalizer.calls == [(original, "en-US")]
    assert narration.calls == [  # type: ignore[attr-defined]
        ("She walked into the great hall and saw twenty elders.", "en-US")
    ]
