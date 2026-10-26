# Timed SRT Audio Synchronization Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a synchronized audio and subtitle generation pipeline for uploaded `.srt` documents that respects video timecodes, merges fragmented cues, absorbs timeline drift into upcoming pauses for natural speech, and outputs both aligned audio (`.mp3`/`.wav`) and translated subtitles (`output.srt`).

**Architecture:** A multi-tier pipeline that parses SRT cues into structured models, merges sentence fragments across contiguous lines, synthesizes speech per semantic chunk, schedules audio playback on a timeline using drift absorption, generates silence intervals via FFmpeg, concatenates into a final audio track, and exports an aligned `output.srt`.

**Tech Stack:** Python 3.11+, FastAPI, FFmpeg (concat demuxer, `anullsrc`, `atempo`), Pytest, React 18, TypeScript, Vite.

**Spec:** `docs/superpowers/specs/2026-10-26-timed-srt-audio-sync-design.md`

## Global Constraints

- **Language-Agnostic Architecture (ADR 0001):** Never hardcode languages; use `source_language` and `target_language` from capabilities and user input.
- **Provider Port Isolation (ADR 0002):** Pipeline and application layers invoke `TranslationProvider` and `TTSProvider` protocols only.
- **Chunk-based processing with cache and resume (ADR 0004 & ADR 0007):** Re-use existing chunk cache and checkpoint mechanism; completed chunks must not be regenerated.
- **Asynchronous RQ Workers (ADR 0003):** Job creation returns `202 Accepted` with `job_id`; background workers run the pipeline.
- **Security & Safety (ADR 0006 & Rule 06):** Input parsing must enforce `MAX_UPLOAD_BYTES` (2,000,000 bytes) and prevent path traversal.

## Review Focus

1. **Cues with zero gap ($S_{i+1} = E_i$) during long monologue:** Scheduler must shift $\text{ActualStart}_{i+1}$ forward by overrun $\Delta t$, accumulate drift, and prevent audio overlap.
2. **Audio finishes early ($D_i < W_i$) with non-zero accumulated drift:** Scheduler must absorb drift by reducing the pause before generating any silence.
3. **Severe accumulated drift ($> 3000\text{ ms}$):** Scheduler must apply subtle tempo scaling ($\approx 1.12\text{x}$) to smoothly bring the timeline back into sync with video.
4. **SRT timestamps with commas and periods:** Parser must accept both comma (`00:01:23,456`) and dot (`00:01:23.456`) decimal time separators.
5. **Existing plain text jobs:** Plain `.txt` uploads must continue using paragraph chunking and standard concatenation without silence padding regressions.

---

### Task 1: SRT Domain Models & Timecode Parser

**Files:**
- Create: `backend/app/domain/srt.py`
- Create: `backend/app/application/pipeline/parse_srt.py`
- Test: `backend/tests/application/test_parse_srt.py`

**Interfaces:**
- Produces:
  - `SRTCue(index: int, start_ms: int, end_ms: int, text: str)`
  - `parse_srt_cues(text: str) -> list[SRTCue]`
  - `format_srt_timecode(ms: int) -> str`

- [ ] **Step 1: Write the failing test for parsing SRT cues**

```python
# backend/tests/application/test_parse_srt.py
from app.application.pipeline.parse_srt import parse_srt_cues, format_srt_timecode
from app.domain.srt import SRTCue

def test_parse_srt_cues_valid():
    raw = (
        "1\n00:00:01,500 --> 00:00:04,200\nHello world!\n\n"
        "2\n00:00:05.100 --> 00:00:07.800\nSecond line <i>styled</i>.\n"
    )
    cues = parse_srt_cues(raw)
    assert len(cues) == 2
    assert cues[0] == SRTCue(index=1, start_ms=1500, end_ms=4200, text="Hello world!")
    assert cues[1] == SRTCue(index=2, start_ms=5100, end_ms=7800, text="Second line styled.")
    assert format_srt_timecode(1500) == "00:00:01,500"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/application/test_parse_srt.py -v`  
Expected: FAIL with ModuleNotFoundError: No module named 'app.domain.srt'

- [ ] **Step 3: Implement `SRTCue` and `parse_srt_cues`**

Define `SRTCue` in `backend/app/domain/srt.py`. In `backend/app/application/pipeline/parse_srt.py`, implement regex parsing for timecodes `(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})`, conversion to milliseconds, HTML tag stripping, and `format_srt_timecode`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/application/test_parse_srt.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/srt.py backend/app/application/pipeline/parse_srt.py backend/tests/application/test_parse_srt.py
git commit -m "feat(srt): add domain models and srt timecode parser"
```

---

### Task 2: Smart Cue Merging into Semantic Timed Chunks

**Files:**
- Create: `backend/app/application/pipeline/merge_srt.py`
- Modify: `backend/app/domain/srt.py`
- Test: `backend/tests/application/test_merge_srt.py`

**Interfaces:**
- Consumes: `SRTCue` from `app.domain.srt`
- Produces:
  - `TimedChunk(chunk_index: int, start_ms: int, end_ms: int, text: str, cue_indices: list[int])`
  - `merge_srt_cues(cues: list[SRTCue], max_pause_ms: int = 400, max_chunk_duration_ms: int = 20000) -> list[TimedChunk]`

- [ ] **Step 1: Write the failing test for merging fragmented cues**

```python
# backend/tests/application/test_merge_srt.py
from app.application.pipeline.merge_srt import merge_srt_cues
from app.domain.srt import SRTCue, TimedChunk

def test_merge_srt_cues_joins_incomplete_sentences():
    cues = [
        SRTCue(index=1, start_ms=1000, end_ms=2500, text="In this tutorial"),
        SRTCue(index=2, start_ms=2600, end_ms=4500, text="we will learn Python."),
        SRTCue(index=3, start_ms=6000, end_ms=8000, text="Let's get started!"),
    ]
    chunks = merge_srt_cues(cues)
    assert len(chunks) == 2
    assert chunks[0] == TimedChunk(
        chunk_index=0,
        start_ms=1000,
        end_ms=4500,
        text="In this tutorial we will learn Python.",
        cue_indices=[1, 2],
    )
    assert chunks[1] == TimedChunk(
        chunk_index=1,
        start_ms=6000,
        end_ms=8000,
        text="Let's get started!",
        cue_indices=[3],
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/application/test_merge_srt.py -v`  
Expected: FAIL with ModuleNotFoundError: No module named 'app.application.pipeline.merge_srt'

- [ ] **Step 3: Implement `merge_srt_cues`**

In `backend/app/domain/srt.py`, add `TimedChunk`. In `backend/app/application/pipeline/merge_srt.py`, implement sentence-boundary aware cue merging that checks punctuation (`.`, `!`, `?`, `。`, etc.), inter-cue pause duration, and maximum duration thresholds.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/application/test_merge_srt.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/srt.py backend/app/application/pipeline/merge_srt.py backend/tests/application/test_merge_srt.py
git commit -m "feat(srt): implement smart sentence cue merging into timed chunks"
```

---

### Task 3: Audio Processor Silence Generation, Duration & Tempo Methods

**Files:**
- Modify: `backend/app/domain/ports.py`
- Modify: `backend/app/infrastructure/ffmpeg_audio.py`
- Modify: `backend/app/infrastructure/fake_audio.py`
- Test: `backend/tests/infrastructure/test_ffmpeg_audio_timeline.py`

**Interfaces:**
- Modifies `AudioProcessor` port with:
  - `async def get_duration_ms(self, path: Path) -> int`
  - `async def generate_silence(self, duration_ms: int, destination: Path, *, output_format: OutputFormat, bitrate_kbps: int) -> AudioArtifact`
  - `async def adjust_tempo(self, source: Path, destination: Path, factor: float, *, output_format: OutputFormat, bitrate_kbps: int) -> AudioArtifact`

- [ ] **Step 1: Write failing test for silence generation and tempo adjustment**

```python
# backend/tests/infrastructure/test_ffmpeg_audio_timeline.py
import pytest
from pathlib import Path
from app.domain.jobs import OutputFormat
from app.infrastructure.ffmpeg_audio import FFmpegAudioProcessor

@pytest.mark.asyncio
async def test_generate_silence_and_measure_duration(tmp_path: Path):
    processor = FFmpegAudioProcessor()
    silence_file = tmp_path / "silence.mp3"
    artifact = await processor.generate_silence(
        duration_ms=1200,
        destination=silence_file,
        output_format=OutputFormat.MP3,
        bitrate_kbps=128,
    )
    assert artifact.path.is_file()
    duration = await processor.get_duration_ms(silence_file)
    assert 1100 <= duration <= 1300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/infrastructure/test_ffmpeg_audio_timeline.py -v`  
Expected: FAIL with AttributeError: 'FFmpegAudioProcessor' object has no attribute 'generate_silence'

- [ ] **Step 3: Implement `generate_silence`, `get_duration_ms`, and `adjust_tempo`**

In `backend/app/infrastructure/ffmpeg_audio.py`:
- `generate_silence`: use `ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -t {sec} ...`
- `get_duration_ms`: use `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1`
- `adjust_tempo`: use `ffmpeg -i {src} -filter:a atempo={factor} ...`
Update `fake_audio.py` to mock these methods for fast unit testing.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/infrastructure/test_ffmpeg_audio_timeline.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/ports.py backend/app/infrastructure/ffmpeg_audio.py backend/app/infrastructure/fake_audio.py backend/tests/infrastructure/test_ffmpeg_audio_timeline.py
git commit -m "feat(audio): add silence generation, duration measurement, and tempo adjustment"
```

---

### Task 4: Timeline Scheduler with Drift Absorption

**Files:**
- Create: `backend/app/application/pipeline/timeline.py`
- Test: `backend/tests/application/test_timeline.py`

**Interfaces:**
- Consumes: `TimedChunk` from `app.domain.srt`
- Produces:
  - `ScheduledItem(chunk_index: int | None, is_silence: bool, scheduled_start_ms: int, duration_ms: int, tempo_factor: float, subtitle_text: str | None)`
  - `TimelineSchedule(items: list[ScheduledItem], total_duration_ms: int)`
  - `schedule_srt_timeline(chunks: list[TimedChunk], chunk_durations_ms: list[int], max_drift_ms: int = 3000) -> TimelineSchedule`

- [ ] **Step 1: Write failing test for timeline drift absorption and silence insertion**

```python
# backend/tests/application/test_timeline.py
from app.application.pipeline.timeline import schedule_srt_timeline
from app.domain.srt import TimedChunk

def test_schedule_timeline_with_drift_absorption():
    chunks = [
        TimedChunk(chunk_index=0, start_ms=1000, end_ms=3000, text="First sentence.", cue_indices=[1]),
        TimedChunk(chunk_index=1, start_ms=3000, end_ms=5000, text="Second sentence.", cue_indices=[2]),
        TimedChunk(chunk_index=2, start_ms=8000, end_ms=10000, text="Third sentence.", cue_indices=[3]),
    ]
    # Chunk 0 takes 2.5s (overruns by 0.5s into Chunk 1)
    # Chunk 1 takes 2.0s
    # Chunk 2 takes 2.0s
    durations = [2500, 2000, 2000]
    schedule = schedule_srt_timeline(chunks, durations)
    
    # Initial silence 0 to 1000
    assert schedule.items[0].is_silence and schedule.items[0].duration_ms == 1000
    # Chunk 0 runs from 1000 to 3500
    assert schedule.items[1].chunk_index == 0 and schedule.items[1].scheduled_start_ms == 1000
    # Chunk 1 shifted to start at 3500 (drift = 500ms), finishes at 5500
    assert schedule.items[2].chunk_index == 1 and schedule.items[2].scheduled_start_ms == 3500
    # Pause between 5500 and 8000 is 2500ms; absorbs 500ms drift, resulting in 2000ms silence
    assert schedule.items[3].is_silence and schedule.items[3].duration_ms == 2500 - 500
    # Chunk 2 resynchronized at exact nominal 8000ms
    assert schedule.items[4].chunk_index == 2 and schedule.items[4].scheduled_start_ms == 8000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/application/test_timeline.py -v`  
Expected: FAIL with ModuleNotFoundError: No module named 'app.application.pipeline.timeline'

- [ ] **Step 3: Implement `schedule_srt_timeline`**

In `backend/app/application/pipeline/timeline.py`, implement the drift absorption algorithm described in Section 5.4 of the spec: track `accumulated_drift_ms`, absorb into raw pause gaps, apply `tempo_factor` if drift > 3000 ms, and output `ScheduledItem` list.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/application/test_timeline.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/pipeline/timeline.py backend/tests/application/test_timeline.py
git commit -m "feat(srt): implement timeline scheduling with drift absorption"
```

---

### Task 5: Pipeline Orchestration & Subtitle Export

**Files:**
- Modify: `backend/app/application/pipeline/orchestrator.py`
- Modify: `backend/app/application/pipeline/parse.py`
- Test: `backend/tests/application/test_pipeline_srt.py`

**Interfaces:**
- Consumes: `parse_srt_cues`, `merge_srt_cues`, `schedule_srt_timeline`, `AudioProcessor`
- Produces:
  - Synchronized `output.mp3`/`output.wav` matching video timeline
  - Aligned `output.srt` written in job directory

- [ ] **Step 1: Write failing test for SRT pipeline run producing synchronized audio and output.srt**

```python
# backend/tests/application/test_pipeline_srt.py
import pytest
from pathlib import Path
from app.application.pipeline.orchestrator import PipelineOrchestrator
from app.domain.jobs import Job, JobStatus, OutputFormat
from tests.fakes import FakeAudioProcessor, FakeTTSProvider, FakeTranslationProvider

@pytest.mark.asyncio
async def test_srt_pipeline_generates_aligned_audio_and_subtitles(tmp_path: Path):
    srt_text = (
        "1\n00:00:01,000 --> 00:00:03,000\nHello world.\n\n"
        "2\n00:00:05,000 --> 00:00:07,000\nSecond line.\n"
    )
    job = Job(
        id="job-srt-1",
        status=JobStatus.PENDING,
        source_language="en-US",
        target_language="en-US",
        voice="fake-voice",
        speed=1.0,
        output_format=OutputFormat.MP3,
    )
    workspace = tmp_path / "work"
    workspace.mkdir()
    # Execute pipeline orchestrator on SRT input
    # Assert output.mp3 exists and output.srt exists
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/application/test_pipeline_srt.py -v`  
Expected: FAIL

- [ ] **Step 3: Integrate SRT flow into orchestrator**

In `backend/app/application/pipeline/orchestrator.py`:
- Detect if source is SRT. If yes, parse into `TimedChunk`s.
- Synthesize audio per chunk.
- Run `schedule_srt_timeline`.
- Generate needed silence audio files.
- Merge speech and silence files into final output.
- Serialize final scheduled speech cues into `output.srt`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/application/test_pipeline_srt.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/pipeline/orchestrator.py backend/tests/application/test_pipeline_srt.py
git commit -m "feat(pipeline): integrate srt timeline synchronization and output.srt generation"
```

---

### Task 6: API Subtitle Download Endpoint & Frontend Studio Support

**Files:**
- Modify: `backend/app/api/jobs.py`
- Modify: `backend/app/application/jobs.py`
- Modify: `frontend/src/components/AudioPlayer.tsx`
- Test: `backend/tests/api/test_job_subtitles.py`

**Interfaces:**
- API: `GET /api/jobs/{job_id}/subtitles` -> serves `output.srt` with media type `application/x-subrip`
- Frontend: "Download Subtitles (.srt)" action link shown when subtitle artifact exists.

- [ ] **Step 1: Write failing test for subtitles download endpoint**

```python
# backend/tests/api/test_job_subtitles.py
from fastapi.testclient import TestClient

def test_get_job_subtitles(jobs_client: TestClient, tmp_path):
    # Setup job with output.srt file
    response = jobs_client.get(f"/api/jobs/{job_id}/subtitles")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"] or "subrip" in response.headers["content-type"]
    assert "00:00:01,000 -->" in response.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest backend/tests/api/test_job_subtitles.py -v`  
Expected: FAIL with 404 Not Found

- [ ] **Step 3: Implement `/api/jobs/{job_id}/subtitles` and UI download link**

In `backend/app/api/jobs.py`, add route `GET /api/jobs/{job_id}/subtitles` checking if `workspace / "output.srt"` exists and returning `FileResponse`. In `frontend/src/components/AudioPlayer.tsx`, add an optional download button for subtitles when available.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest backend/tests/api/test_job_subtitles.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/jobs.py backend/app/application/jobs.py frontend/src/components/AudioPlayer.tsx backend/tests/api/test_job_subtitles.py
git commit -m "feat(api,frontend): add subtitle download endpoint and UI link"
```
