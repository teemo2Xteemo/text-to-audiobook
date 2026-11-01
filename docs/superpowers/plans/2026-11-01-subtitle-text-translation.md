# Subtitle & Text Translation (no audio) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A third job mode that translates `.srt` / `.vtt` / `.ass` / `.txt` / pasted text and produces a downloadable file (no TTS), with source cue count and timecodes preserved so the result imports into CapCut.

**Architecture:** New `JobMode.SUBTITLE`. Pure modules in `application/pipeline/` parse each subtitle format into a lossless `SubtitleDocument` (raw text kept verbatim, only cue text slots replaced), group cues into sentences, translate sentence batches through the existing `_translate_chunks` loop (retry / cache / checkpoint) with a separator count check and per-sentence fallback, split translations back onto cues, and render. The orchestrator walks `QUEUED → PARSING → TRANSLATING → MERGING → COMPLETED` and writes `output.<fmt>` into the job workspace; a new `GET /api/jobs/{id}/document` serves it.

**Tech Stack:** Python 3 / FastAPI / pytest / ruff (backend); React + TypeScript / Vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-11-01-subtitle-text-translation-design.md`

## Global Constraints

- Language-agnostic: no source/target language or pair in business logic; script differences decided by text features (presence of whitespace), never by language code.
- Domain/application code calls `TranslationProvider` only (via `translate_chunk`), never a vendor API.
- HTTP creates the job and returns `job_id`; the worker runs the pipeline.
- Per-chunk retry, cache, checkpoint, resume; successful chunks (and successful fallback sentences) are never re-translated.
- Uploads and filenames are untrusted; the original filename is sanitised before it is stored or used in `Content-Disposition`.
- Subtitle input: output format = detected input format; cue count, order, indices, start/end timecodes unchanged.
- Text input (pasted or `.txt`): output format chosen by the user, default `txt`.
- Format detected **by content**, not extension.
- Each output cue is a single line.
- Download filename: `<sanitised-stem>.<target_language>.<ext>`; no filename → stem `translation`.
- UI copy from the spec: tab label `Chỉ dịch`; completion line `Đã dịch N / N dòng`.
- Output encoding UTF-8; `.ass` header/fields text-identical (not byte-identical).
- Backend checks: `cd backend && pytest -q && ruff check . && ruff format --check .`; frontend: `cd frontend && npm test && npm run typecheck`.

## Review Focus

1. Pasted SRT with Windows line endings / BOM / trailing spaces → still detected as SRT and round-trips (Task 2 test `test_detect_srt_with_bom_and_crlf`).
2. Auto-generated captions with no punctuation → sentence groups must stay bounded, not one giant group (Task 3 test `test_unpunctuated_cues_group_is_capped`).
3. Provider returns fewer/more lines than sentences (LLM merges lines, NLLB collapses newlines) → per-sentence fallback, no cross-sentence drift (Task 5 test `test_count_mismatch_falls_back_per_sentence`).
4. Translation shorter than the number of cues it must cover (e.g. 3 cues → "Ok.") → every cue still non-empty (Task 3 test `test_split_short_translation_keeps_every_cue_nonempty`).
5. Malicious upload filename (`../../x.srt`, `a"b.srt`, `\r\n` injection) → stored stem is safe, header is safe (Task 6 test `test_source_name_is_sanitised`).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `backend/app/domain/subtitles.py` (create) | `DocumentFormat`, `CueSlot`, `SubtitleDocument` types |
| `backend/app/domain/jobs.py` (modify) | `JobMode.SUBTITLE`, `Job.document_format`, `Job.source_name`, `TRANSLATING → MERGING` edge |
| `backend/app/application/pipeline/subtitle_formats.py` (create) | `detect_document_format`, `parse_document` (SRT / VTT / ASS), `render_document`, `write_timed_document` |
| `backend/app/application/pipeline/subtitle_sentences.py` (create) | `group_sentences`, `split_translation`, `split_long_line` |
| `backend/app/application/pipeline/subtitle_batch.py` (create) | `BATCH_SEPARATOR`, `join_batch`, `split_batch`, `pack_batches`, `estimate_duration_ms` |
| `backend/app/application/pipeline/orchestrator.py` (modify) | `_run_subtitle`, batched fallback inside `_translate_chunks` |
| `backend/app/application/jobs.py` (modify) | `CreateJobCommand` fields, SUBTITLE validation, `sanitize_source_name`, `output_document_path` |
| `backend/app/api/parse.py`, `schemas.py`, `jobs.py` (modify) | form/JSON fields, `.ass` allowlist, `document_url`, `/document` endpoint |
| `frontend/src/api/types.ts`, `client.ts`, `lib/jobUi.ts`, `lib/constants.ts`, `components/JobForm.tsx`, `App.tsx` (modify) | tab, format selector, download |

---

### Task 1: Domain — mode, document format, job fields, transition

**Files:**
- Create: `backend/app/domain/subtitles.py`
- Modify: `backend/app/domain/jobs.py`
- Test: `backend/tests/domain/test_job_record.py`, `backend/tests/domain/test_job_status.py`

**Interfaces:**
- Produces:
  - `class DocumentFormat(StrEnum)`: `TXT="txt"`, `SRT="srt"`, `VTT="vtt"`, `ASS="ass"`; `SUBTITLE_FORMATS = frozenset({SRT, VTT, ASS})`.
  - `@dataclass(frozen=True) class CueSlot: start_ms: int; end_ms: int; text: str; raw: str; prefix: str = ""; translatable: bool = True` — `text` is the plain text sent to translation, `raw` the original text field (rendered verbatim when not translatable), `prefix` leading `{…}` override tags re-attached on render.
  - `@dataclass(frozen=True) class SubtitleDocument: format: DocumentFormat; parts: tuple[str | CueSlot, ...]` with property `cues -> list[CueSlot]` (the `CueSlot` parts in order).
  - `JobMode.SUBTITLE = "subtitle"`.
  - `Job.document_format: DocumentFormat | None = None`, `Job.source_name: str | None = None`, `Job.line_total: int = 0` (translated cues/lines, for the completion copy); serialised in `to_dict` only when set (`line_total` when > 0); `from_dict` reads them (`document_format` invalid → `ValueError`).
  - `Job.__post_init__`: `mode is SUBTITLE` requires `document_format` not `None`.
  - `can_transition(TRANSLATING, MERGING) is True` (skip TTS stages); all other edges unchanged.

- [ ] **Step 1: Write failing tests**

```python
def test_subtitle_job_round_trips_document_fields():
    job = _job(mode=JobMode.SUBTITLE, document_format=DocumentFormat.ASS, source_name="ep01")
    data = job.to_dict()
    assert data["mode"] == "subtitle" and data["document_format"] == "ass" and data["source_name"] == "ep01"
    assert Job.from_dict(data) == job

def test_translate_job_dict_has_no_document_fields():
    assert "document_format" not in _job().to_dict()

def test_subtitle_job_requires_document_format():
    with pytest.raises(ValueError):
        _job(mode=JobMode.SUBTITLE)

def test_translating_may_skip_to_merging():
    assert can_transition(JobStatus.TRANSLATING, JobStatus.MERGING)
    assert not can_transition(JobStatus.PARSING, JobStatus.MERGING)
```

- [ ] **Step 2: Run** `cd backend && pytest tests/domain -q` — Expected: FAIL (`DocumentFormat` / `SUBTITLE` undefined).
- [ ] **Step 3: Implement** the types and fields above. Add the skip edge as a separate `_SKIPS = {JobStatus.TRANSLATING: JobStatus.MERGING}` checked in `can_transition` alongside `_FORWARD`.
- [ ] **Step 4: Run** `pytest tests/domain -q` — Expected: PASS (existing transition tests still pass).
- [ ] **Step 5: Commit** `git commit -m "feat(domain): subtitle job mode and document format"`

---

### Task 2: Lossless subtitle parse / render / write

**Files:**
- Create: `backend/app/application/pipeline/subtitle_formats.py`
- Test: `backend/tests/application/test_subtitle_formats.py`

**Interfaces:**
- Consumes: Task 1 types; `parse_timecode_line`, `format_srt_timecode`, `is_sound_effect_line` from `parse_srt.py`; `is_srt_document` from `parse.py`.
- Produces:
  - `detect_document_format(text: str) -> DocumentFormat` — ASS if a `[Script Info]` or `[Events]` section line and at least one `Dialogue:` line; VTT if first non-empty line starts with `WEBVTT`; SRT if `is_srt_document(text)`; else `TXT`. Normalises `\r\n`/`\r` and strips BOM before checking.
  - `parse_document(text: str) -> SubtitleDocument | None` — `None` for `TXT`. Input normalised to `\n`, BOM stripped.
  - `render_document(doc: SubtitleDocument, texts: Sequence[str]) -> str` — `len(texts) == len(doc.cues)` else `ValueError`; each slot renders as `slot.prefix + texts[i]` when `translatable`, else `slot.raw`.
  - `write_timed_document(fmt: DocumentFormat, cues: Sequence[tuple[int, int, str]]) -> str` — new SRT (`1\n00:00:00,000 --> 00:00:02,000\ntext\n`, blocks separated by a blank line), VTT (`WEBVTT\n\n` + `00:00:00.000 --> …` blocks), ASS (fixed header below + `Dialogue: 0,H:MM:SS.cc,H:MM:SS.cc,Default,,0,0,0,,text`). `fmt=TXT` → `ValueError`.

Parsing rules (these decide the lossless behaviour):
- SRT: per block, everything up to and including the timecode line + `\n` is a raw `str` part; the cue text lines (joined `\n`) are the slot's `raw`; blank-line separators stay raw parts.
- VTT: header block, `NOTE`, `STYLE`, `REGION` blocks are raw parts; cue blocks as SRT (identifier line optional, timing line kept whole including settings).
- ASS: everything outside `[Events]` raw. In `[Events]`, read the `Format:` line for the field count; split each `Dialogue:` line on `,` with `maxsplit=fields-1`; raw part = everything up to the Text field, slot = Text field. `Comment:` lines raw. Times `H:MM:SS.cc`.
- Slot `text`: `raw` with `\N`, `\n`, `\h` → space, `<…>` and `{…}` tags removed, `html.unescape`, whitespace collapsed. `prefix` = leading run of `{…}` blocks in `raw` (e.g. `{\an8}`); `<i>` is not re-attached. `translatable=False` when `text` is empty or `is_sound_effect_line(text)`.
- New ASS header for `write_timed_document`:

```
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,60,60,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
```

- [ ] **Step 1: Write failing tests** (fixtures inline as strings)

```python
@pytest.mark.parametrize("fixture", [SRT_SAMPLE, VTT_SAMPLE, ASS_SAMPLE])
def test_round_trip_with_raw_text_is_identity(fixture):
    doc = parse_document(fixture)
    assert render_document(doc, [c.raw for c in doc.cues]) == fixture

def test_detect_srt_with_bom_and_crlf():
    assert detect_document_format("﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nHi\r\n") is DocumentFormat.SRT

def test_detect_formats():
    assert detect_document_format(VTT_SAMPLE) is DocumentFormat.VTT
    assert detect_document_format(ASS_SAMPLE) is DocumentFormat.ASS
    assert detect_document_format("Just a story.\n\nSecond paragraph.") is DocumentFormat.TXT

def test_srt_slots_keep_timing_and_clean_text():
    doc = parse_document("1\n00:00:01,000 --> 00:00:02,500\n{\\an8}<i>Hello</i>\nthere\n")
    (cue,) = doc.cues
    assert (cue.start_ms, cue.end_ms, cue.text, cue.prefix) == (1000, 2500, "Hello there", "{\\an8}")

def test_sound_effect_cue_rendered_verbatim():
    doc = parse_document("1\n00:00:01,000 --> 00:00:02,000\n[Music]\n")
    assert not doc.cues[0].translatable
    assert render_document(doc, ["IGNORED"]) == "1\n00:00:01,000 --> 00:00:02,000\n[Music]\n"

def test_ass_only_text_field_changes():
    doc = parse_document(ASS_SAMPLE)  # Dialogue: 0,0:00:01.00,0:00:02.00,Default,Bob,0,0,0,,{\an8}Hi, you\Nthere
    out = render_document(doc, ["Xin chào"] * len(doc.cues))
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,Bob,0,0,0,,{\\an8}Xin chào" in out
    assert out.split("[Events]")[0] == ASS_SAMPLE.split("[Events]")[0]

def test_vtt_keeps_header_notes_and_cue_settings():
    doc = parse_document(VTT_SAMPLE)
    out = render_document(doc, ["X"] * len(doc.cues))
    assert out.startswith("WEBVTT") and "NOTE" in out and "align:start" in out

def test_write_timed_srt():
    assert write_timed_document(DocumentFormat.SRT, [(0, 2000, "Xin chào")]) == "1\n00:00:00,000 --> 00:00:02,000\nXin chào\n"
```

`ASS_SAMPLE` must include a `Comment:` line and a Text field containing a comma; `VTT_SAMPLE` a `NOTE` block, a cue identifier and `align:start`.

- [ ] **Step 2: Run** `pytest tests/application/test_subtitle_formats.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `subtitle_formats.py` per the rules above.
- [ ] **Step 4: Run** the same — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(pipeline): lossless srt/vtt/ass documents"`

---

### Task 3: Sentence grouping and split-back

**Files:**
- Create: `backend/app/application/pipeline/subtitle_sentences.py`
- Test: `backend/tests/application/test_subtitle_sentences.py`

**Interfaces:**
- Consumes: `CueSlot`; `is_sentence_terminal` (`merge_srt.py`); `SRT_PARAGRAPH_GAP_MS` (`parse.py`).
- Produces:
  - `MAX_CUES_PER_SENTENCE = 5`
  - `group_sentences(cues: Sequence[CueSlot]) -> list[list[int]]` — indices of translatable cues, consecutive, in order. A group closes after a cue whose text `is_sentence_terminal`, before a gap `> SRT_PARAGRAPH_GAP_MS` to the next cue, at `MAX_CUES_PER_SENTENCE` cues, or at a non-translatable cue. Every translatable cue is in exactly one group.
  - `split_translation(text: str, weights: Sequence[int]) -> list[str]` — `len(result) == len(weights)`, pieces in order, stripped. Target cut points at cumulative weight share of `len(text)`; each cut snaps to the nearest whitespace or punctuation within ±40 % of the average piece length, else cuts at that character. Every piece non-empty when `text` has at least `len(weights)` non-space characters; otherwise empty slots repeat the last non-empty piece.
  - `split_long_line(text: str) -> list[str]` — for text → timed output. Max chars per cue: `84` if the text contains whitespace, else `32`. Prefer cuts after sentence punctuation, then commas, then whitespace, then characters.

- [ ] **Step 1: Write failing tests**

```python
def test_two_cue_sentence_grouped():
    cues = [_cue(0, 1000, "I think that"), _cue(1100, 2000, "we should go."), _cue(2100, 3000, "Now!")]
    assert group_sentences(cues) == [[0, 1], [2]]

def test_long_gap_breaks_group():
    assert group_sentences([_cue(0, 1000, "and then"), _cue(5000, 6000, "later")]) == [[0], [1]]

def test_unpunctuated_cues_group_is_capped():
    cues = [_cue(i * 1000, i * 1000 + 900, f"word {i}") for i in range(12)]
    groups = group_sentences(cues)
    assert all(len(g) <= MAX_CUES_PER_SENTENCE for g in groups)
    assert [i for g in groups for i in g] == list(range(12))

def test_sound_effect_cue_excluded_and_breaks_group():
    cues = [_cue(0, 1000, "Wait"), _cue(1000, 2000, "[Music]", translatable=False), _cue(2000, 3000, "here.")]
    assert group_sentences(cues) == [[0], [2]]

def test_split_by_weight_snaps_to_space():
    assert split_translation("Tôi nghĩ rằng chúng ta nên đi.", [12, 13]) == ["Tôi nghĩ rằng", "chúng ta nên đi."]

def test_split_without_spaces_cuts_characters():
    pieces = split_translation("我认为我们应该走了", [1, 1, 1])
    assert len(pieces) == 3 and "".join(pieces) == "我认为我们应该走了" and all(pieces)

def test_split_short_translation_keeps_every_cue_nonempty():
    assert all(split_translation("Ok.", [10, 10, 10, 10]))

def test_split_long_line_respects_limit():
    assert all(len(p) <= 84 for p in split_long_line("Một câu rất dài. " * 20))
```

- [ ] **Step 2: Run** `pytest tests/application/test_subtitle_sentences.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** per the interface.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(pipeline): subtitle sentence grouping and split-back"`

---

### Task 4: Batch join/split helpers and timing estimate

**Files:**
- Create: `backend/app/application/pipeline/subtitle_batch.py`
- Test: `backend/tests/application/test_subtitle_batch.py`

**Interfaces:**
- Produces:
  - `BATCH_SEPARATOR = "\n"` (unit texts never contain newlines; each unit is one line).
  - `join_batch(units: Sequence[str]) -> str`
  - `split_batch(translated: str, expected: int) -> list[str] | None` — strip, split on `\n`, drop blank lines, strip each; `None` when the count ≠ `expected`.
  - `pack_batches(units: Sequence[str], max_chars: int) -> list[list[int]]` — consecutive unit indices whose joined length ≤ `max_chars`; an oversize unit is its own batch.
  - `estimate_duration_ms(text: str) -> int` — `70 ms/char` when text has whitespace, else `200 ms/char`, clamped to `[1000, 7000]`.

- [ ] **Step 1: Write failing tests**

```python
def test_split_batch_matches_count():
    assert split_batch(" A\n\nB \n", 2) == ["A", "B"]

def test_count_mismatch_returns_none():
    assert split_batch("A B", 2) is None

def test_pack_batches_respects_max_chars():
    assert pack_batches(["aaaa", "bbbb", "cccc"], max_chars=9) == [[0, 1], [2]]

def test_duration_clamped():
    assert estimate_duration_ms("Hi") == 1000
    assert estimate_duration_ms("x " * 200) == 7000
```

- [ ] **Step 2: Run** `pytest tests/application/test_subtitle_batch.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(pipeline): subtitle batch helpers"`

---

### Task 5: Orchestrator — subtitle run with batched fallback

**Files:**
- Modify: `backend/app/application/pipeline/orchestrator.py` (`run` ~L102, `_translate_chunks` ~L446)
- Modify: `backend/tests/fakes.py` (add `MergingLinesTranslator`)
- Test: `backend/tests/application/test_pipeline_subtitle.py` (reuse the `_build_orchestrator` / `_make_job` pattern from `test_pipeline_srt.py`)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `run()`: `if job.mode is JobMode.SUBTITLE: return await self._run_subtitle(job, text, workspace=workspace, checkpoints=checkpoints)` right after advancing to `PARSING`.
  - `_run_subtitle(...)`: resolve source with `resolve_source_language` on the joined translatable text; build **units** (single-line strings):
    - subtitle doc (`parse_document` not `None`): one unit per `group_sentences` group = the group's cue texts joined by a space.
    - text input: one unit per non-empty line of `prepare_narration_text(text)` (each line whitespace-collapsed), remembering the blank-line layout.
  - Chunks: `pack_batches(units, self._max_chars)` → `Chunk(id=f"chunk-{n:03d}", text=join_batch(batch_units), index=n)`; `chunk_total = len(chunks)`; `line_total` = number of translatable cues (subtitle doc) or non-empty lines (text).
  - `TRANSLATING`: `source_language == target_language` → `_pass_through_chunks(..., stage=STAGE_TRANSLATED, active_status=JobStatus.TRANSLATING, suffix=".translated.txt")`; else `_translate_chunks(job, chunks, workspace, checkpoints, batched=True)`.
  - `_translate_chunks(..., batched: bool = False)`: when `batched`, the provider call inside `_translate` is `_translate_batch(job, chunk, workspace, checkpoints, lock) -> str`: translate `chunk.text` once; if `split_batch` returns the right count, use it; else translate each line separately — each fallback line written to `chunks/{chunk.id}.s{i:03d}.translated.txt`, checkpointed as id `f"{chunk.id}.s{i:03d}"` / `STAGE_TRANSLATED` under `lock`, and reused when already complete. Returns `join_batch(lines)` so the chunk artifact always has the right line count. Chunk retry, cache and checkpoint unchanged.
  - `MERGING`: read each chunk's `.translated.txt`, `split_batch` back into unit translations, then:
    - subtitle doc: per group, `split_translation(unit, [len(cues[i].text) for i in group])`; `render_document`.
    - text → `TXT`: rebuild the original line / blank-line layout with translated lines.
    - text → SRT/VTT/ASS: `split_long_line` each translated line; back-to-back cues from 0 ms using `estimate_duration_ms`; `write_timed_document`.
  - Write `workspace / f"output.{job.document_format.value}"` (UTF-8); missing/empty → `DomainError(ErrorType.STORAGE_FAILED, ...)`. Advance `TRANSLATING → MERGING → COMPLETED`; never enters TTS stages.

- [ ] **Step 1: Write failing tests**

```python
async def test_srt_cue_count_and_timing_preserved(tmp_path):
    out = await _run(tmp_path, SRT_FIVE_CUES, document_format=DocumentFormat.SRT, target="vi-VN")
    src, dst = parse_document(SRT_FIVE_CUES).cues, parse_document(out).cues
    assert [(c.start_ms, c.end_ms) for c in dst] == [(c.start_ms, c.end_ms) for c in src]
    assert all(c.text for c in dst if c.translatable)

async def test_subtitle_job_skips_tts(tmp_path):
    job, tts = await _run_job(tmp_path, SRT_FIVE_CUES)
    assert job.status is JobStatus.COMPLETED and tts.calls == []

async def test_count_mismatch_falls_back_per_sentence(tmp_path):
    translation = MergingLinesTranslator(["en-US", "vi-VN"])  # multi-line input → lines joined by " "
    out = await _run(tmp_path, SRT_FIVE_CUES, translation=translation)
    assert translation.single_line_calls == len(group_sentences(parse_document(SRT_FIVE_CUES).cues))
    # each cue holds only text derived from its own sentence group (fake tags output with the source text)

async def test_resume_does_not_retranslate_completed_chunks_or_fallback_lines(tmp_path):
    # run 1: provider fails on the 3rd fallback line of chunk-002 after chunk-001 completed
    # run 2 on the same workspace: assert no provider call repeats chunk-001's text or the 2 checkpointed lines

async def test_text_input_txt_keeps_layout(tmp_path):
    out = await _run(tmp_path, "Line one.\nLine two.\n\nPara two.", document_format=DocumentFormat.TXT)
    assert out.count("\n\n") == 1 and len(out.strip().split("\n")) == 4

async def test_text_input_to_srt_builds_back_to_back_cues(tmp_path):
    cues = parse_document(await _run(tmp_path, "Hello.\nWorld.", document_format=DocumentFormat.SRT)).cues
    assert cues[0].start_ms == 0 and cues[1].start_ms == cues[0].end_ms

async def test_same_language_passes_through(tmp_path):
    out = await _run(tmp_path, SRT_FIVE_CUES, source="en-US", target="en-US")
    assert [c.text for c in parse_document(out).cues] == [c.text for c in parse_document(SRT_FIVE_CUES).cues]
```

- [ ] **Step 2: Run** `pytest tests/application/test_pipeline_subtitle.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `_run_subtitle`, `_translate_batch` and the `batched` flag.
- [ ] **Step 4: Run** `pytest tests/application -q` — Expected: PASS; existing pipeline tests unchanged.
- [ ] **Step 5: Commit** `git commit -m "feat(pipeline): subtitle translation run with batch fallback"`

---

### Task 6: JobService — create, sanitise name, output path

**Files:**
- Modify: `backend/app/application/jobs.py` (`CreateJobCommand` ~L44, `create` ~L100, paths ~L420)
- Test: `backend/tests/application/test_create_job.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces:
  - `CreateJobCommand.document_format: str | None = None`, `CreateJobCommand.source_filename: str | None = None`.
  - `sanitize_source_name(filename: str | None) -> str | None` — basename stem, characters outside `[\w.-]` → `_`, runs of `_` collapsed, strip `._`, cap 80 chars; empty → `None`.
  - `create()` for `SUBTITLE`: languages and translation profile validated exactly as translate mode; `voice`, `tts_engine`, `normalize_text`, non-continuous `srt_timing` → `INVALID_INPUT`; no TTS preflight, `voice=None`. `document_format` = `detect_document_format(text)` when it is in `SUBTITLE_FORMATS`, else the requested value parsed as `DocumentFormat` (invalid → `INVALID_INPUT "document_format must be txt, srt, vtt or ass"`), default `TXT`. `source_name = sanitize_source_name(command.source_filename)`. `output_format` keeps the default `mp3` (unused).
  - Other modes with `document_format` set → `INVALID_INPUT`.
  - `output_document_path(job: Job) -> Path` — same traversal guard as `output_audio_path`; `directory / f"output.{job.document_format.value}"`; `document_format is None` → `DomainError(INVALID_INPUT)`.

- [ ] **Step 1: Write failing tests**

```python
async def test_subtitle_mode_detects_srt_over_requested_txt(service):
    job = await service.create(_cmd(mode=JobMode.SUBTITLE, text=SRT_TEXT, document_format="txt"))
    assert job.document_format is DocumentFormat.SRT

async def test_pasted_text_defaults_to_txt(service):
    assert (await service.create(_cmd(mode=JobMode.SUBTITLE, text="Hello"))).document_format is DocumentFormat.TXT

async def test_subtitle_mode_rejects_voice(service):
    with pytest.raises(DomainError):
        await service.create(_cmd(mode=JobMode.SUBTITLE, voice="v1"))

async def test_translate_mode_rejects_document_format(service):
    with pytest.raises(DomainError):
        await service.create(_cmd(mode=JobMode.TRANSLATE, document_format="srt"))

def test_source_name_is_sanitised():
    assert sanitize_source_name('../../ep 01\r\n".srt') == "ep_01"
    assert sanitize_source_name(None) is None
```

- [ ] **Step 2: Run** `pytest tests/application/test_create_job.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(jobs): create subtitle translation jobs"`

---

### Task 7: API — fields, `.ass` upload, document download

**Files:**
- Modify: `backend/app/api/parse.py` (L23 MIME set, L132 / L189 mode branches, L391 extension check), `backend/app/api/schemas.py` (`CreateJobJsonBody`, `JobStatusResponse`), `backend/app/api/jobs.py`
- Test: `backend/tests/api/test_job_document.py` (create), `backend/tests/api/test_jobs.py`

**Interfaces:**
- Consumes: Task 6.
- Produces:
  - JSON body and multipart form accept `document_format`; multipart passes `upload.filename` as `source_filename`. `SUBTITLE` takes the translate branch (source and target required).
  - Upload allowlist adds `.ass` and MIME `text/x-ssa`; error text `"file must be a .txt, .srt, .vtt, or .ass document"`.
  - `JobStatusResponse.document_url: str | None` (`/api/jobs/{id}/document` when completed and the file is non-empty) `document_format: DocumentFormat | None` and `line_total: int`; `audio_url` is `None` for `SUBTITLE` jobs.
  - `GET /api/jobs/{job_id}/document`: 400 bad UUID, 404 unknown, 409 not completed, 404 missing artifact (same envelopes as `/subtitles`); `FileResponse` media type `text/plain` / `application/x-subrip` / `text/vtt` / `text/x-ssa`, `filename=f"{job.source_name or 'translation'}.{job.target_language}.{fmt}"`, attachment.

- [ ] **Step 1: Write failing tests**

```python
def test_upload_ass_creates_subtitle_job(client):
    r = client.post("/api/jobs", data={"mode": "subtitle", "source_language": "auto", "target_language": "vi-VN"},
                    files={"file": ("ep01.ass", ASS_TEXT, "text/x-ssa")})
    assert r.status_code == 202

def test_document_download_filename(client, completed_subtitle_job):
    r = client.get(f"/api/jobs/{completed_subtitle_job.id}/document")
    assert r.status_code == 200
    assert 'filename="ep01.vi-VN.srt"' in r.headers["content-disposition"]

def test_document_not_ready_is_409(client, queued_subtitle_job):
    assert client.get(f"/api/jobs/{queued_subtitle_job.id}/document").status_code == 409

def test_status_has_document_url_not_audio_url(client, completed_subtitle_job):
    body = client.get(f"/api/jobs/{completed_subtitle_job.id}").json()
    assert body["document_url"].endswith("/document") and body["audio_url"] is None
```

- [ ] **Step 2: Run** `pytest tests/api -q` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest -q && ruff check . && ruff format --check .` — Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(api): subtitle job fields and document download"`

---

### Task 8: Frontend — "Chỉ dịch" tab and download

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/lib/constants.ts` (`ALLOWED_FILE_EXTENSIONS`), `frontend/src/lib/jobUi.ts`, `frontend/src/components/JobForm.tsx` (mode tabs ~L534, `validateFile` ~L384, `accept` L1017), `frontend/src/App.tsx` (~L35)
- Test: `frontend/src/components/JobForm.test.tsx`, `frontend/src/lib/jobUi.test.ts`, `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: Task 7 API.
- Produces:
  - `JobMode = "translate" | "narrate" | "subtitle"`; `DocumentFormat = "txt" | "srt" | "vtt" | "ass"`; `CreateJobInput.document_format?: DocumentFormat`; `JobStatusResponse.document_url?: string | null`, `document_format?: DocumentFormat | null`, `line_total?: number`. `createJob` sends `document_format` when set (form and JSON).
  - `detectSubtitleFormat(text: string): DocumentFormat | null` in `jobUi.ts` — same content rules as backend Task 2, used for pasted text; files use their extension (`.srt/.vtt/.ass` → that format, `.txt` → `null`). Backend stays the source of truth.
  - `pipelineStepsForMode("subtitle")` → Queue, Parse, Translate, Write (`merging`), Ready. `progressCopy("merging", …, "subtitle")` → `"Writing file"`.
  - JobForm third tab `Chỉ dịch`: language route + translation profile + input; hides voice, speed, engine, audio format, srt timing, normalize. Format buttons `.txt .srt .vtt .ass` (`aria-pressed`), default `txt`; when input is a subtitle, the detected one is pressed and the others disabled. `accept` adds `.ass,text/x-ssa`; error copy `"Only .txt, .srt, .vtt, and .ass files are supported."`.
  - App: when `document_url` is present, show `Đã dịch {line_total} / {line_total} dòng` and a download link to `document_url`; no audio player for subtitle jobs.

- [ ] **Step 1: Write failing tests**

```tsx
it("subtitle tab hides voice controls and defaults to txt", async () => {
  render(<JobForm {...props} />);
  await user.click(screen.getByRole("tab", { name: /Chỉ dịch/ }));
  expect(screen.queryByLabelText(/voice/i)).toBeNull();
  expect(screen.getByRole("button", { name: ".txt" })).toHaveAttribute("aria-pressed", "true");
});

it("uploading .srt locks format to srt", async () => {
  // upload new File([...], "ep01.srt") → ".srt" aria-pressed="true", ".txt" disabled
});

it("submits mode subtitle with document_format", async () => {
  // choose ".vtt" with pasted text → createJob called with objectContaining({ mode: "subtitle", document_format: "vtt" })
});

it("detectSubtitleFormat recognises pasted srt", () => {
  expect(detectSubtitleFormat("1\n00:00:01,000 --> 00:00:02,000\nHi")).toBe("srt");
  expect(detectSubtitleFormat("plain")).toBeNull();
});

it("completed subtitle job shows download link", async () => {
  // job with document_url and line_total 3 → link href === document_url, text "Đã dịch 3 / 3 dòng"
});
```

- [ ] **Step 2: Run** `cd frontend && npm test` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npm test && npm run typecheck` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat(ui): translate-only tab with file download"`
