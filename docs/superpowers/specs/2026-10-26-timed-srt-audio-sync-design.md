# Design Document: Timed SRT Audio Synchronization Pipeline

**Date:** 2026-10-26  
**Status:** Approved by Builder Review (Ready for User Sign-Off)  
**Scope:** Project  
**Author:** Pair Programming Session with User

---

## 1. Intent & The Why

Users currently can upload `.srt` files or paste subtitle content into the application. However, the existing pipeline strips all timecodes and cue indices, treating the file as continuous text. When users upload `.srt` files extracted from video sources (such as YouTube videos or lectures), they expect the generated audio track to be temporally synchronized with the original video ("flow theo mốc thời gian được mô tả trong file .srt giống như file audio download từ youtube về"). 

Furthermore, the user emphasized **prioritizing natural speech flow ("ưu tiên sự tự nhiên nhất")**, requiring the system to avoid jarring mechanical speedups and instead allow dialogue to naturally expand and borrow adjacent pauses ("dời nhẹ mốc nói rồi tự bù ở khoảng lặng tiếp theo").

---

## 2. Goals, Non-Goals, & Anti-Goals

### Goals
- **SRT Cue Preservation & Structured Ingestion:** Parse `.srt` files into structured timed cues (`index`, `start_ms`, `end_ms`, `text`) while maintaining backwards-compatibility for plain text story workflows.
- **Smart Sentence Merging:** Merge subtitle cues that break mid-sentence across consecutive lines (common in YouTube auto-captions) based on punctuation and timestamp proximity.
- **Timeline-Aware Audio Alignment:** Align synthesized audio clips to the exact starting timestamps of each cue/sentence, inserting silence audio for unvoiced intervals.
- **Timeline Drift Absorption (User's Call):** When a sentence takes longer than its subtitle slot during rapid dialogue, allow subsequent cue start times to shift forward slightly, absorbing this drift at the next silence gap without robotic speedups.
- **Length-Aware Translation Prompting:** When translation is enabled, provide character/duration hints to the translator to encourage concise translation fitting the video pace.
- **Synchronized Subtitle Output:** Generate and save a matching translated `.srt` (`output.srt`) alongside `output.mp3`/`output.wav` in the job workspace when an SRT job completes.

### Non-Goals
- Real-time video player preview or video rendering (the system generates the synchronized audio track and subtitle file; video multiplexing remains client/user side).
- Voice cloning or multi-speaker diarization from video audio.
- Supporting binary subtitle formats (e.g. PGS/SUP). Text formats (SRT and WebVTT) are supported.

### Anti-Goals
- **No mechanical chipmunk distortion:** Never apply aggressive speedups (>1.25x) just to fit an arbitrary tight cue boundary when silence ahead can absorb it.
- **No audio collision/bleed:** Never allow speech from cue $N$ to play simultaneously over speech from cue $N+1$.
- **No breaking existing plain-text audiobook flows:** Standard `.txt` and pasted stories must follow the existing continuous paragraph chunking without silence padding regressions.

---

## 3. Constraints & Repository Rules

- **Language-Agnostic Architecture (ADR 0001):** The pipeline must not hardcode any source or target language (e.g., Vietnamese, English, Japanese). All translation and TTS parameters must be resolved through `source_language`, `target_language`, and provider capabilities.
- **Provider Port Isolation (ADR 0002):** Domain and pipeline code call `TranslationProvider` and `TTSProvider` protocols. No vendor-specific SDK imports inside application orchestration.
- **Chunk-based processing with cache and resume (ADR 0004 & ADR 0007):** SRT timed chunks must integrate with the existing workspace checkpointing and disk store. Completed chunks must not be regenerated on retry.
- **Asynchronous RQ Workers (ADR 0003):** Job creation returns `202 Accepted` with `job_id`. The worker processes the timeline assembly and reports progress via `status.json`.
- **Security & Safety (ADR 0006 & Rule 06):** Input parsing must enforce `MAX_UPLOAD_BYTES`, sanitize filenames against directory traversal, and avoid running shell interpolations.

---

## 4. Approaches Considered & Tradeoffs

During brainstorming, three core approaches were evaluated:

### Approach 1: Timeline-Aware Chunks & Flexible Silence Padding
- **Mechanism:** Merge broken cues into semantic sentences; synthesize audio; borrow silence gaps between cues; insert silence blocks via ffmpeg concat demuxer.
- **Pros:** Completely natural speech, CPU-efficient, fits directly into existing chunk cache and concat pipeline.
- **Cons:** Requires timeline scheduling logic in the orchestrator.

### Approach 2: FFmpeg Multi-Track Mixing (`adelay` + `amix`)
- **Mechanism:** Assign absolute delay timestamps to every cue audio file and mix onto a blank timeline.
- **Pros:** Simple start time calculation.
- **Cons:** Long FFmpeg filter chains for video with hundreds of cues cause command-line buffer overflows, high RAM consumption, and inevitable overlapping speech if a cue overruns.

### Approach 3: Two-Pass Resynthesis
- **Mechanism:** Synthesize each cue; measure duration; if it overruns, re-invoke TTS with increased speed parameter.
- **Pros:** Pure TTS audio without post-processing time stretch.
- **Cons:** Doubles network latency, cost, and provider load on long transcripts.

### Chosen Approach: Unified Multi-Tier Hybrid with Drift Absorption
The user selected the unified hybrid pipeline, specifically choosing **timeline drift absorption** to prioritize natural voiceover:
1. **Tier 1 (Text & Translation):** Semantic sentence merging across cues + length-aware translation prompt.
2. **Tier 2 (Natural TTS):** Synthesize speech at natural 1.0x rate (or user-specified rate).
3. **Tier 3 (Timeline Scheduling & Drift Absorption):** Track speech duration. If audio overruns, shift downstream speech and absorb drift at subsequent pause intervals.
4. **Tier 4 (Safety Net):** If accumulated drift exceeds a safety threshold (3.0s), apply subtle `atempo` (1.1x–1.15x) to gradually realign with video timing.

---

## 5. Detailed Design & Architecture

### 5.1 Domain Models (`backend/app/domain/srt.py`)

```python
@dataclass(frozen=True)
class SRTCue:
    index: int
    start_ms: int
    end_ms: int
    text: str

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

@dataclass(frozen=True)
class TimedChunk:
    chunk_index: int
    start_ms: int
    end_ms: int
    text: str
    cue_indices: list[int]
```

### 5.2 Ingestion & SRT Detection (`backend/app/application/pipeline/parse.py`)
- `is_srt_document(text)` checks if the input is an SRT/WebVTT file.
- If SRT is detected, `parse_srt_cues(text)` produces a list of `SRTCue`.
- `merge_srt_cues(cues: list[SRTCue]) -> list[TimedChunk]`:
  - Merges adjacent cues into complete sentences if:
    - The preceding cue does not end with sentence-terminating punctuation (`.`, `!`, `?`, `。`, `！`, `？`).
    - The gap between cue $N$ end and cue $N+1$ start is $\le 400\text{ ms}$.
    - Combined duration does not exceed chunk threshold (e.g. 15–20 seconds).

### 5.3 Translation with Length Awareness
- If `source_language != target_language`:
  - The `TranslationProvider` is invoked per `TimedChunk`.
  - For providers supporting contextual instructions (e.g. Ollama/TranslateGemma/LLM), pass timing metadata: `"Keep translation concise to fit within approximately X seconds"`.

### 5.4 Timeline Scheduling with Drift Absorption

Let chunk $i$ have nominal start time $S_i$, audio duration $D_i$, and nominal end time $E_i$.
The scheduler maintains an `accumulated_drift_ms` (initialized to 0):

1. **Scheduled Start Time:**  
   $$\text{ActualStart}_i = S_i + \text{accumulated\_drift\_ms}$$
2. **Audio End Time:**  
   $$\text{ActualEnd}_i = \text{ActualStart}_i + D_i$$
3. **Next Chunk Nominal Start:** $S_{i+1}$ (or video end duration if final chunk).
4. **Pause Calculation:**  
   - Nominal gap before next chunk: $\text{NominalGap} = S_{i+1} - E_i$.
   - If $\text{ActualEnd}_i > S_{i+1} + \text{accumulated\_drift\_ms}$:
     - Speech overruns past the scheduled boundary into the next cue.
     - Increase drift: $\text{accumulated\_drift\_ms} = \text{ActualEnd}_i - S_{i+1}$.
     - Silence gap before chunk $i+1$ is 0 ms.
   - If $\text{ActualEnd}_i \le S_{i+1} + \text{accumulated\_drift\_ms}$:
     - Speech finished before the next cue starts.
     - Available pause $\text{raw\_pause} = (S_{i+1} + \text{accumulated\_drift\_ms}) - \text{ActualEnd}_i$.
     - Absorb drift: If $\text{accumulated\_drift\_ms} > 0$, reduce pause to absorb drift:
       $$\text{absorbed} = \min(\text{raw\_pause}, \text{accumulated\_drift\_ms})$$
       $$\text{accumulated\_drift\_ms} = \text{accumulated\_drift\_ms} - \text{absorbed}$$
       $$\text{final\_silence\_ms} = \text{raw\_pause} - \text{absorbed}$$
     - Insert a silence block of $\text{final\_silence\_ms}$ between chunk $i$ and chunk $i+1$.
5. **Safety Net:** If $\text{accumulated\_drift\_ms} > 3000\text{ ms}$, apply a gentle `atempo=1.12` to chunk $i$ to reduce duration by ~10% until drift returns under 1000 ms.

### 5.5 Silence Generation & Concat Merging
- Silence audio blocks are generated via FFmpeg (`anullsrc=r=44100:cl=mono`) matching the job's `output_format` and sample rate into `workspace / "silence" / f"silence_{i}.{ext}"`.
- The list of alternating speech and silence audio paths is written to `.concat.txt` and merged via the existing `concat_argv`.

### 5.6 Subtitle Output (`output.srt`)
- For each `TimedChunk` $i$, record its final scheduled timing:
  $$\text{cue\_start} = \text{ActualStart}_i$$
  $$\text{cue\_end} = \text{ActualEnd}_i$$
- Write `output.srt` into the job's storage directory containing the translated/spoken sentence text and the exact matching audio timestamps.
- Serve `output.srt` via API endpoint `GET /api/jobs/{job_id}/subtitles` (or query param).

---

## 6. Resolved Builder Calls (Architectural Decisions)

1. **Drift Absorption over Hard-Trimming (User's Call):** Dialogue naturally expands and contracts into nearby pauses; video alignment is recovered at every silent gap.
2. **Output Subtitle Granularity (Builder's Call):** `output.srt` outputs 1 subtitle entry per merged semantic `TimedChunk` with timestamps reflecting actual audio speech intervals ($[\text{ActualStart}_i, \text{ActualEnd}_i]$), ensuring clean readability and exact 1:1 synchronization with the generated voice track.
3. **Language-Agnostic TTS (Builder's Call):** In adherence to ADR 0001, no hardcoded character/syllable heuristic tables are used. Dynamic durations are directly measured from synthesized audio files.

---

## 7. What's Left to the Builder

- Regular expression parser supporting both standard SRT (`00:01:23,456`) and WebVTT (`00:01:23.456`).
- Progress tracking weight allocation across pipeline stages (parse, translate, synthesize, silence, merge).
- Frontend UI button in the studio download section to download the synchronized `.srt` file alongside the `.mp3`/`.wav`.
