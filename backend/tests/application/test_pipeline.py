from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

from app.application.pipeline.checkpoint import STAGE_TRANSLATED, CheckpointStore
from app.application.pipeline.conservative_narration import ConservativeNarrationProcessor
from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.domain.audio import Voice
from app.domain.chunking import chunk_text
from app.domain.errors import ErrorType
from app.domain.jobs import Job, JobStatus, OutputFormat
from app.domain.languages import AUTO_SOURCE_LANGUAGE
from app.domain.ports import NarrationProcessor
from tests.fakes import (
    FakeAudioProcessor,
    FakeLanguageDetector,
    FakeNarrationProcessor,
    FakeTranslationProvider,
    FakeTTSProvider,
    InMemoryJobStore,
)

THREE_SENTENCES = "Alpha is first. Bravo is second. Charlie is third."
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


def test_translator_failure_fails_job_without_retry(tmp_path: Path) -> None:
    job = _job()
    translation = FakeTranslationProvider(["en-US", "ko-KR"], error=RuntimeError("boom"))
    orchestrator, workspace, translation, _, tts, _, _, jobs = _orchestrator(
        tmp_path, job=job, translation=translation
    )
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.TRANSLATION_FAILED
    assert len(translation.calls) == 1
    assert tts.calls == []
    assert jobs.jobs[job.id].status is JobStatus.FAILED
    assert not (workspace / "output.mp3").exists()


def test_unsupported_language_fails_job(tmp_path: Path) -> None:
    job = _job(target_language="ko-KR")
    translation = FakeTranslationProvider(["en-US"])
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(
        tmp_path, job=job, translation=translation, voices=_voices_for("ko-KR")
    )
    result = _run(orchestrator, job, workspace)
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.UNSUPPORTED_LANGUAGE


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
