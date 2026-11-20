from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.application.pipeline.subtitle_formats import parse_document
from app.application.pipeline.subtitle_sentences import group_sentences
from app.domain.audio import Voice
from app.domain.errors import ErrorType
from app.domain.jobs import Job, JobMode, JobStatus, OutputFormat
from app.domain.retry import RetryPolicy
from app.domain.subtitles import DocumentFormat
from app.infrastructure.checkpoint_fs import CheckpointFilesystem
from tests.fakes import (
    FakeAudioProcessor,
    FakeLanguageDetector,
    FakeNarrationProcessor,
    FakeTranslationProvider,
    FakeTTSProvider,
    InMemoryJobStore,
    MergingLinesTranslator,
)

SRT_FIVE_CUES = (
    "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n"
    "2\n00:00:02,000 --> 00:00:03,500\nI think we\n\n"
    "3\n00:00:03,000 --> 00:00:04,000\nshould go.\n\n"
    "4\n00:00:04,200 --> 00:00:05,000\n[Music]\n\n"
    "5\n00:00:05,000 --> 00:00:06,500\nNow we leave.\n"
)

VTT_CUES = (
    "WEBVTT\n\n"
    "NOTE\nkeep me\n\n"
    "intro\n00:00:01.000 --> 00:00:02.000 align:start\nHello there.\n\n"
    "00:00:03.000 --> 00:00:04.000\nSecond line.\n"
)

ASS_CUES = (
    "[Script Info]\n"
    "ScriptType: v4.00+\n\n"
    "[Events]\n"
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    "Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,note\n"
    "Dialogue: 0,0:00:01.00,0:00:02.50,Default,Bob,0,0,0,,Hello, friend.\n"
    "Dialogue: 0,0:00:03.00,0:00:04.00,Default,Bob,0,0,0,,{\\an8}Next line.\n"
)


def _voices_for(*languages: str) -> list[Voice]:
    return [
        Voice(id=f"voice-{language}", language=language, label=language) for language in languages
    ]


def _make_job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "id": "job-subtitle",
        "status": JobStatus.QUEUED,
        "source_language": "en-US",
        "target_language": "vi-VN",
        "voice": None,
        "speed": 1.0,
        "output_format": OutputFormat.MP3,
        "output_bitrate_kbps": 128,
        "mode": JobMode.SUBTITLE,
        "document_format": DocumentFormat.SRT,
    }
    values.update(overrides)
    return Job(**values)  # type: ignore[arg-type]


def _build(
    tmp_path: Path,
    job: Job,
    *,
    translation: FakeTranslationProvider | MergingLinesTranslator | None = None,
    max_chars: int = 1200,
    max_attempts: int = 2,
) -> tuple[PipelineOrchestrator, Path, FakeTTSProvider, InMemoryJobStore]:
    workspace = tmp_path / "work" / job.id
    tts = FakeTTSProvider(_voices_for(job.target_language), tmp_path / "tts")
    store = InMemoryJobStore()
    orchestrator = PipelineOrchestrator(
        translation=translation
        or FakeTranslationProvider([job.source_language, job.target_language]),
        tts=tts,
        narration=FakeNarrationProcessor(),
        detector=FakeLanguageDetector(language_code=job.source_language),
        audio=FakeAudioProcessor(),
        jobs=store,
        checkpoint_storage_factory=CheckpointFilesystem,
        retry_policy=RetryPolicy(max_attempts=max_attempts, backoff_seconds=0),
        max_chars=max_chars,
    )
    return orchestrator, workspace, tts, store


def _run(
    orchestrator: PipelineOrchestrator,
    job: Job,
    workspace: Path,
    text: str,
    *,
    deadline: float | None = None,
) -> Job:
    return asyncio.run(orchestrator.run(job, text, workspace=workspace, deadline=deadline))


def _output(
    tmp_path: Path,
    text: str,
    *,
    document_format: DocumentFormat = DocumentFormat.SRT,
    source: str = "en-US",
    target: str = "vi-VN",
    translation: FakeTranslationProvider | MergingLinesTranslator | None = None,
    max_chars: int = 1200,
    max_attempts: int = 2,
) -> str:
    job = _make_job(
        source_language=source,
        target_language=target,
        document_format=document_format,
    )
    orchestrator, workspace, _, _ = _build(
        tmp_path,
        job,
        translation=translation,
        max_chars=max_chars,
        max_attempts=max_attempts,
    )
    result = _run(orchestrator, job, workspace, text)
    assert result.status is JobStatus.COMPLETED
    return (workspace / f"output.{document_format.value}").read_text(encoding="utf-8")


def test_srt_cue_count_and_timing_preserved(tmp_path: Path) -> None:
    out = _output(tmp_path, SRT_FIVE_CUES, document_format=DocumentFormat.SRT)
    src = parse_document(SRT_FIVE_CUES)
    dst = parse_document(out)
    assert src is not None and dst is not None
    assert [(cue.start_ms, cue.end_ms) for cue in dst.cues] == [
        (cue.start_ms, cue.end_ms) for cue in src.cues
    ]
    assert len(dst.cues) == len(src.cues)
    assert all(cue.text for cue in dst.cues if cue.translatable)
    assert "[Music]" in out


def test_vtt_and_ass_keep_cue_count_and_timecodes(tmp_path: Path) -> None:
    vtt = _output(tmp_path / "vtt", VTT_CUES, document_format=DocumentFormat.VTT)
    src_vtt = parse_document(VTT_CUES)
    dst_vtt = parse_document(vtt)
    assert src_vtt is not None and dst_vtt is not None
    assert [(cue.start_ms, cue.end_ms) for cue in dst_vtt.cues] == [
        (cue.start_ms, cue.end_ms) for cue in src_vtt.cues
    ]
    assert vtt.startswith("WEBVTT") and "NOTE" in vtt and "align:start" in vtt

    ass = _output(tmp_path / "ass", ASS_CUES, document_format=DocumentFormat.ASS)
    src_ass = parse_document(ASS_CUES)
    dst_ass = parse_document(ass)
    assert src_ass is not None and dst_ass is not None
    assert [(cue.start_ms, cue.end_ms) for cue in dst_ass.cues] == [
        (cue.start_ms, cue.end_ms) for cue in src_ass.cues
    ]
    assert "Comment:" in ass
    assert "Hello, friend" not in ass or "[vi-VN]" in ass
    assert ass.split("[Events]")[0] == ASS_CUES.split("[Events]")[0]


def test_subtitle_job_skips_tts(tmp_path: Path) -> None:
    job = _make_job()
    orchestrator, workspace, tts, store = _build(tmp_path, job)
    seen: list[JobStatus] = []
    original = store.save

    async def _save(saved: Job) -> None:
        seen.append(saved.status)
        await original(saved)

    store.save = _save  # type: ignore[method-assign]
    result = _run(orchestrator, job, workspace, SRT_FIVE_CUES)
    assert result.status is JobStatus.COMPLETED
    assert tts.calls == []
    assert JobStatus.PREPARING_TTS not in seen
    assert JobStatus.GENERATING_AUDIO not in seen
    assert not (workspace / "output.mp3").exists()
    assert not (workspace / "chapters.json").exists()
    assert (workspace / "output.srt").is_file()


def test_count_mismatch_falls_back_per_sentence(tmp_path: Path) -> None:
    translation = MergingLinesTranslator(["en-US", "vi-VN"])
    out = _output(tmp_path, SRT_FIVE_CUES, translation=translation, max_chars=5000)
    doc = parse_document(SRT_FIVE_CUES)
    assert doc is not None
    groups = group_sentences(doc.cues)
    assert translation.single_line_calls == len(groups)
    rendered = parse_document(out)
    assert rendered is not None
    # The sound-effect cue is not a group. Alpha/Beta style words live in the
    # source sentence that owned the cue, never in a neighbour sentence.
    cue_text = [cue.text for cue in rendered.cues]
    assert "Hello there." in cue_text[0]
    assert "should go." in cue_text[1] or "should go." in cue_text[2]
    assert "Hello there." not in cue_text[2]
    assert "Now we leave." in cue_text[4]
    assert "Hello there." not in cue_text[4]
    assert "[Music]" in out


def test_resume_does_not_retranslate_completed_chunks_or_fallback_lines(tmp_path: Path) -> None:
    blocks = [
        f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},800\nSentence {index} stays.\n"
        for index in range(1, 7)
    ]
    source = "\n".join(blocks)
    translation = MergingLinesTranslator(["en-US", "vi-VN"], fail_on="Sentence 6 stays.")
    job = _make_job(id="job-resume")
    orchestrator, workspace, tts, _ = _build(
        tmp_path, job, translation=translation, max_chars=60, max_attempts=1
    )
    failed = _run(orchestrator, job, workspace, source)
    assert failed.status is JobStatus.FAILED
    assert failed.error_type is ErrorType.TRANSLATION_FAILED
    assert tts.calls == []
    done = {call[0] for call in translation.calls}
    assert "Sentence 1 stays." in done
    assert "Sentence 5 stays." in done
    assert any("Sentence 1 stays." in call and "\n" in call for call in done)

    translation.calls.clear()
    translation.single_line_calls = 0
    resumed = replace_queued(failed)
    finished = _run(orchestrator, resumed, workspace, source)
    assert finished.status is JobStatus.COMPLETED
    repeated = {call[0] for call in translation.calls}
    assert "Sentence 1 stays." not in repeated
    assert "Sentence 5 stays." not in repeated
    assert not any("Sentence 1 stays." in call and "\n" in call for call in repeated)
    assert "Sentence 6 stays." in repeated
    out = (workspace / "output.srt").read_text(encoding="utf-8")
    parsed = parse_document(out)
    assert parsed is not None
    assert len(parsed.cues) == 6
    assert [(cue.start_ms, cue.end_ms) for cue in parsed.cues] == [
        (cue.start_ms, cue.end_ms)
        for cue in (parse_document(source).cues)  # type: ignore[union-attr]
    ]


def test_text_input_txt_keeps_layout(tmp_path: Path) -> None:
    out = _output(
        tmp_path,
        "Line one.\nLine two.\n\nPara two.",
        document_format=DocumentFormat.TXT,
    )
    assert out.count("\n\n") == 1
    assert len(out.strip().split("\n")) == 4


def test_text_input_exports_srt_vtt_and_ass(tmp_path: Path) -> None:
    text = "Hello.\nWorld."
    srt = _output(tmp_path / "srt", text, document_format=DocumentFormat.SRT)
    cues = parse_document(srt)
    assert cues is not None
    assert cues.cues[0].start_ms == 0
    assert cues.cues[1].start_ms == cues.cues[0].end_ms

    vtt = _output(tmp_path / "vtt", text, document_format=DocumentFormat.VTT)
    assert vtt.startswith("WEBVTT\n")
    vtt_doc = parse_document(vtt)
    assert vtt_doc is not None and len(vtt_doc.cues) == 2

    ass = _output(tmp_path / "ass", text, document_format=DocumentFormat.ASS)
    assert "Style: Default,Arial,64,&H00FFFFFF," in ass
    assert ass.count("Dialogue:") == 2
    ass_doc = parse_document(ass)
    assert ass_doc is not None
    assert ass_doc.cues[0].start_ms == 0
    assert ass_doc.cues[1].start_ms == ass_doc.cues[0].end_ms


def test_same_language_passes_through(tmp_path: Path) -> None:
    out = _output(tmp_path, SRT_FIVE_CUES, source="en-US", target="en-US")
    src = parse_document(SRT_FIVE_CUES)
    dst = parse_document(out)
    assert src is not None and dst is not None
    assert [cue.text for cue in dst.cues] == [cue.text for cue in src.cues]


def test_run_budget_yields_and_resume_skips_finished_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    monkeypatch.setattr("app.application.pipeline.orchestrator.time.monotonic", clock)
    blocks = [
        f"{index}\n00:00:{index:02d},000 --> 00:00:{index:02d},800\nBudget line {index}.\n"
        for index in range(1, 7)
    ]
    source = "\n".join(blocks)
    job = _make_job(id="job-budget")
    translation = _ClockTranslation(clock, ["en-US", "vi-VN"])
    orchestrator, workspace, tts, _ = _build(tmp_path, job, translation=translation, max_chars=40)
    first = _run(orchestrator, job, workspace, source, deadline=0.5)
    assert first.continue_requested is True
    assert first.status not in {JobStatus.COMPLETED, JobStatus.FAILED}
    assert JobStatus.PREPARING_TTS != first.status
    assert tts.calls == []
    finished_text = {call[0] for call in translation.calls}
    assert finished_text

    clock.now = 0
    translation.calls.clear()
    second = _run(orchestrator, first, workspace, source, deadline=clock.now + 1000)
    assert second.status is JobStatus.COMPLETED
    assert second.continue_requested is False
    assert tts.calls == []
    assert finished_text.isdisjoint({call[0] for call in translation.calls})
    assert not (workspace / "chapters.json").exists()


def test_unknown_mode_still_fails(tmp_path: Path) -> None:
    job = _make_job(mode=JobMode.UNKNOWN, document_format=None)
    orchestrator, workspace, tts, _ = _build(tmp_path, job)
    result = _run(orchestrator, job, workspace, "Hello")
    assert result.status is JobStatus.FAILED
    assert result.error_type is ErrorType.INVALID_INPUT
    assert tts.calls == []


def replace_queued(job: Job) -> Job:
    from dataclasses import replace

    return replace(
        job,
        status=JobStatus.QUEUED,
        error_type=None,
        message=None,
        error_stage=None,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _ClockTranslation(FakeTranslationProvider):
    def __init__(self, clock: _Clock, languages: list[str]) -> None:
        super().__init__(languages)
        self._clock = clock

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        self._clock.now += 1
        return await super().translate(text, source_language, target_language)
