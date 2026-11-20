"""Per-chapter files are assembled before the book finishes and are not rebuilt."""

import asyncio
import io
import os
import threading
import zipfile
from dataclasses import replace
from pathlib import Path

from app.application.pipeline.chapter_assembly import assemble_ready_chapters
from app.application.pipeline.chapter_zip import iter_stored_zip
from app.application.pipeline.chapters import plan_chapter_chunks, split_chapters
from app.application.pipeline.checkpoint import STAGE_NORMALIZED
from app.domain.chapters import ChapterManifest, ChapterRecord, ChapterStatus
from app.domain.chunking import CHUNK_MAX_CHARS, Chunk
from app.domain.errors import DomainError, ErrorType
from app.domain.jobs import JobMode, JobStatus, OutputFormat
from app.infrastructure.chapter_manifest_fs import FilesystemChapterManifest
from app.infrastructure.fake_audio import FakeAudioProcessor
from app.providers.tts.fake import FakeTTSProvider
from tests.application.test_pipeline import _job, _orchestrator, _run, _voices_for

_STORY = "Chapter 1: Dawn\n\nThe sun rose.\n\nChapter 2: Dusk\n\nThe sun set."


def test_chunks_do_not_cross_chapter_boundaries() -> None:
    chapters = split_chapters(_STORY)
    chunks, spans = plan_chapter_chunks(chapters, max_chars=CHUNK_MAX_CHARS)
    assert len(spans) == 2
    assert spans[0].ordinal == 1
    assert spans[1].ordinal == 2
    assert spans[0].chunk_last < spans[1].chunk_first
    assert "Chapter 2" not in chunks[0].text
    assert "Chapter 1" not in chunks[1].text


def test_plain_text_and_srt_are_one_span() -> None:
    plain, spans = plan_chapter_chunks(split_chapters("Just a short story."))
    assert len(spans) == 1
    assert spans[0].ordinal == 1
    assert spans[0].kind.value == "chapter"
    assert plain[0].text == "Just a short story."


def test_chapter_file_exists_before_the_job_finishes(tmp_path: Path) -> None:
    job = _job(
        id="22222222-2222-2222-2222-222222222222",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
    )
    orchestrator, workspace, _, _, tts, _, audio, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=500,
    )

    class _StopAfterFirst(FakeTTSProvider):
        def __init__(self) -> None:
            super().__init__(_voices_for("en-US"), tmp_path / "tts-scratch")
            self._done = 0

        async def synthesize(self, text, language, voice, settings):  # type: ignore[no-untyped-def]
            if self._done:
                raise DomainError(ErrorType.TTS_FAILED, "tts failed")
            self._done += 1
            return await super().synthesize(text, language, voice, settings)

    orchestrator._tts = _StopAfterFirst()
    result = _run(orchestrator, job, workspace, text=_STORY)
    assert result.status is JobStatus.FAILED
    assert (workspace / "chapters" / "0001.mp3").is_file()
    assert not (workspace / "chapters" / "0002.mp3").exists()
    manifest = FilesystemChapterManifest(workspace).load()
    assert manifest is not None
    assert manifest.chapters[0].status.value == "ready"
    assert manifest.chapters[1].status.value == "pending"
    assert audio.merge_calls
    del tts


def test_resume_does_not_rebuild_a_ready_chapter(tmp_path: Path, monkeypatch) -> None:
    from tests.application.test_pipeline import _Clock, _ClockTTS, _patch_clock

    clock = _Clock()
    _patch_clock(monkeypatch, clock)
    job = _job(
        id="33333333-3333-3333-3333-333333333333",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
    )
    voices = _voices_for("en-US")
    tts = _ClockTTS(clock, voices, tmp_path / "tts-scratch", step=1)
    orchestrator, workspace, _, _, _, _, audio, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=voices,
        tts=tts,
        max_chars=500,
    )
    first = _run(orchestrator, job, workspace, text=_STORY, deadline=0.5)
    assert first.status is JobStatus.GENERATING_AUDIO
    ready = workspace / "chapters" / "0001.mp3"
    assert ready.is_file()
    assert not (workspace / "chapters" / "0002.mp3").exists()
    signature = ready.read_bytes()
    chapter_one = _chapter_merges(audio.merge_calls, "chunk-001")
    assert len(chapter_one) == 1
    clock.now = 0
    second = _run(orchestrator, first, workspace, text=_STORY, deadline=100)
    assert second.status is JobStatus.COMPLETED
    assert ready.read_bytes() == signature
    assert len(_chapter_merges(audio.merge_calls, "chunk-001")) == 1
    assert (workspace / "chapters" / "0002.mp3").is_file()


def _chapter_merges(calls: list[list[Path]], chunk_name: str) -> list[list[Path]]:
    return [
        call
        for call in calls
        if any(path.name.startswith(chunk_name) for path in call)
        and all(path.name.startswith(chunk_name) or "chapter-pause" in path.name for path in call)
    ]


def test_translate_mode_writes_chapter_files_without_pauses(tmp_path: Path) -> None:
    job = _job(mode=JobMode.TRANSLATE)
    orchestrator, workspace, _, _, _, _, audio, _ = _orchestrator(
        tmp_path, job=job, max_chars=500, chapter_pause_ms=1500
    )
    result = _run(orchestrator, job, workspace, text=_STORY)
    assert result.status is JobStatus.COMPLETED
    assert audio.silence_calls == []
    manifest = FilesystemChapterManifest(workspace).load()
    assert manifest is not None and len(manifest.chapters) == 2
    assert (workspace / "chapters" / "0001.mp3").is_file()
    assert (workspace / "chapters" / "0002.mp3").is_file()
    assert (workspace / "output.mp3").is_file()


def test_legacy_checkpoint_job_does_not_grow_a_manifest(tmp_path: Path) -> None:
    from app.application.pipeline.checkpoint import STAGE_TRANSLATED, CheckpointStore
    from app.domain.chunking import chunk_text
    from app.infrastructure.checkpoint_fs import CheckpointFilesystem

    text = "One sentence here. Another sentence there."
    chunks = chunk_text(text, max_chars=20)
    job = _job(status=JobStatus.TRANSLATING, chunk_total=len(chunks))
    workspace = tmp_path / "jobs" / job.id
    translated = workspace / "chunks" / f"{chunks[0].id}.translated.txt"
    translated.parent.mkdir(parents=True)
    translated.write_text(chunks[0].text, encoding="utf-8")
    CheckpointStore(storage=CheckpointFilesystem(workspace)).record(
        chunks[0].id, STAGE_TRANSLATED, translated
    )
    orchestrator, workspace, _, _, _, _, _, _ = _orchestrator(tmp_path, job=job, max_chars=20)
    result = _run(orchestrator, job, workspace, text=text)
    assert result.status is JobStatus.COMPLETED
    assert not (workspace / "chapters.json").exists()
    assert (workspace / "output.mp3").is_file()


def test_zip_stream_round_trip_and_order(tmp_path: Path) -> None:
    first = tmp_path / "a.mp3"
    second = tmp_path / "b.mp3"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    names = ["0002 - Later.mp3", "0001 - Earlier.mp3"]
    payload = b"".join(
        asyncio.run(_collect(iter_stored_zip([(names[1], first), (names[0], second)])))
    )
    assert payload.find(b"PK\x03\x04") == 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["0001 - Earlier.mp3", "0002 - Later.mp3"]
        assert archive.read("0001 - Earlier.mp3") == b"one"
        info = archive.getinfo("0001 - Earlier.mp3")
        assert info.compress_type == zipfile.ZIP_STORED
        assert info.file_size == 3


async def _collect(stream):  # type: ignore[no-untyped-def]
    return [chunk async for chunk in stream]


def test_changed_chunk_rebuilds_only_its_chapter(tmp_path: Path) -> None:
    job = _job(
        id="44444444-4444-4444-4444-444444444444",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.NARRATE,
        voice="voice-en-US",
    )
    orchestrator, workspace, _, _, tts, _, audio, _ = _orchestrator(
        tmp_path,
        job=job,
        languages=["en-US"],
        voices=_voices_for("en-US"),
        max_chars=500,
    )
    done = _run(orchestrator, job, workspace, text=_STORY)
    assert done.status is JobStatus.COMPLETED
    (workspace / "chunks" / "chunk-001.translated.txt").write_text("NOT THE PLAN", encoding="utf-8")
    before_one = len(_chapter_merges(audio.merge_calls, "chunk-001"))
    before_two = len(_chapter_merges(audio.merge_calls, "chunk-002"))
    calls_before = len(tts.calls)
    again = _run(
        orchestrator,
        replace(done, status=JobStatus.QUEUED, error_type=None, message=None, chunk_current=0),
        workspace,
        text=_STORY,
    )
    assert again.status is JobStatus.COMPLETED
    assert len(tts.calls) == calls_before + 1
    assert len(_chapter_merges(audio.merge_calls, "chunk-001")) == before_one + 1
    assert len(_chapter_merges(audio.merge_calls, "chunk-002")) == before_two
    manifest = FilesystemChapterManifest(workspace).load()
    assert manifest is not None
    assert manifest.chapters[0].status is ChapterStatus.READY
    assert manifest.chapters[1].status is ChapterStatus.READY


def test_failed_assembly_retries_that_chapter_only(tmp_path: Path) -> None:
    class _FailOnce(FakeAudioProcessor):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        async def merge(self, sources, destination, **kwargs):  # type: ignore[no-untyped-def]
            if destination.name == "0002.mp3" and not self.failed:
                self.failed = True
                raise DomainError(ErrorType.AUDIO_PROCESSING_FAILED, "chapter 2 assembly failed")
            return await super().merge(sources, destination, **kwargs)

    audio = _FailOnce()
    job = _job(
        id="55555555-5555-5555-5555-555555555555",
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
        audio=audio,
        max_chars=500,
    )
    failed = _run(orchestrator, job, workspace, text=_STORY)
    assert failed.status is JobStatus.FAILED
    assert failed.message is not None and "chapter 2" in failed.message
    manifest = FilesystemChapterManifest(workspace).load()
    assert manifest is not None
    assert manifest.chapters[0].status is ChapterStatus.READY
    assert (workspace / "chapters" / "0001.mp3").is_file()
    assert manifest.chapters[1].status is ChapterStatus.FAILED
    assert manifest.chapters[1].attempts == 1
    assert manifest.chapters[1].error_type == ErrorType.AUDIO_PROCESSING_FAILED.value
    merges_one = len(_chapter_merges(audio.merge_calls, "chunk-001"))

    retried = _run(
        orchestrator,
        replace(failed, status=JobStatus.QUEUED, error_type=None, message=None, chunk_current=0),
        workspace,
        text=_STORY,
    )
    assert retried.status is JobStatus.COMPLETED
    manifest = FilesystemChapterManifest(workspace).load()
    assert manifest is not None
    assert manifest.chapters[1].status is ChapterStatus.READY
    assert manifest.chapters[1].attempts == 2
    assert manifest.chapters[1].duration_ms is not None
    assert len(_chapter_merges(audio.merge_calls, "chunk-001")) == merges_one


def test_assembly_saves_once_per_ready_span_not_per_chunk(tmp_path: Path) -> None:
    class _Store:
        def __init__(self, manifest: ChapterManifest) -> None:
            self.manifest = manifest
            self.saves = 0
            self.loads = 0

        def load(self) -> ChapterManifest:
            self.loads += 1
            return self.manifest

        def save(self, manifest: ChapterManifest) -> None:
            self.saves += 1
            self.manifest = manifest

    class _Checks:
        def __init__(self) -> None:
            self.done: set[tuple[str, str]] = set()

        def is_complete(self, chunk_id: str, stage: str) -> bool:
            return (chunk_id, stage) in self.done

        def record(self, chunk_id: str, stage: str, artifact: Path) -> None:
            del artifact
            self.done.add((chunk_id, stage))

    records = [
        ChapterRecord(
            ordinal=ordinal,
            kind="chapter",
            chapter_number=ordinal,
            title_source=f"Chapter {ordinal}",
            title=f"Chapter {ordinal}",
            chunk_first=(ordinal - 1) * 2 + 1,
            chunk_last=ordinal * 2,
        )
        for ordinal in (1, 2, 3)
    ]
    manifest = ChapterManifest(
        version=1,
        max_chars=20,
        chapter_aware=True,
        detector="v2",
        chapters=records,
    )
    chunks = [
        Chunk(
            id=f"chunk-{index:03d}",
            text=f"text {index}",
            index=index,
            chapter=((index - 1) // 2) + 1,
        )
        for index in range(1, 7)
    ]
    store = _Store(manifest)
    checks = _Checks()
    audio = FakeAudioProcessor()
    job = _job(output_format=OutputFormat.MP3)

    async def _drive() -> None:
        for chunk in chunks:
            normalized = tmp_path / "audio" / f"{chunk.id}.normalized.mp3"
            normalized.parent.mkdir(parents=True, exist_ok=True)
            normalized.write_bytes(b"x")
            checks.done.add((chunk.id, STAGE_NORMALIZED))
            await assemble_ready_chapters(
                audio=audio,
                job=job,
                chunks=chunks,
                workspace=tmp_path,
                checkpoints=checks,  # type: ignore[arg-type]
                manifest=manifest,
                store=store,  # type: ignore[arg-type]
                only_chunk_index=chunk.index,
            )

    asyncio.run(_drive())
    assert store.loads == 0
    assert store.saves == 3
    assert all(record.status is ChapterStatus.READY for record in manifest.chapters)
    assert all(record.duration_ms is not None for record in manifest.chapters)


def test_zip_cancel_closes_the_source(tmp_path: Path) -> None:
    source = tmp_path / "0001.mp3"
    source.write_bytes(b"x" * (9 * 1024 * 1024))

    async def _cancel() -> None:
        stream = iter_stored_zip([("0001 - Big.mp3", source)])
        first = await stream.__anext__()
        assert first
        await stream.aclose()

    asyncio.run(_cancel())
    alive = [
        thread
        for thread in threading.enumerate()
        if thread.name == "chapter-zip" and thread.is_alive()
    ]
    assert alive == []
    target = str(source.resolve())
    leaked = []
    for entry in Path("/proc/self/fd").iterdir():
        try:
            linked = os.readlink(entry)
        except OSError:
            continue
        if linked == target:
            leaked.append(linked)
    assert leaked == []
