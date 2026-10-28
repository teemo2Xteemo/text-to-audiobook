> **Status:** ✅ **FINAL spec, implementation-ready** (consolidates the original draft, the resolutions in [comment 6449006442](https://github.com/teemo2Xteemo/text-to-audiobook/issues/28#issuecomment-6449006442), the UX review in [comment 6449573949](https://github.com/teemo2Xteemo/text-to-audiobook/issues/28#issuecomment-6449573949), and the mixed-language decision) · **Base:** default branch, `HEAD` as of 2026-10-28 · **Previous draft body:** kept in the issue edit history · **Related:** #29 (per-chapter download), #30 (upload limit), #31 (long-job timeout) · **Convention:** after approval, copy to `docs/superpowers/specs/YYYY-MM-DD-narrate-mode-design.md`.

## 1. Summary & motivation

Every job today runs through translation: `PipelineOrchestrator.run()` (`backend/app/application/pipeline/orchestrator.py`) → `parse_source` → `chunk_source` → **`_translate_chunks`** → `_narrate_chunks` → `_generate_audio` → `_merge`. People who already have text in the language they want to hear still pay for an LLM call, wait for it, and are limited to languages the translation provider supports.

Concrete problems in the current code:
- For plain text, the main path always calls `translate_chunk`, even when `source_language == target_language`. The SRT path does not: `_run_srt` already falls back to `_pass_through_chunks`.
- With `TRANSLATION_PROVIDER=fake`, `FakeTranslationProvider.translate` returns `"[vi-VN] text"`, so the prefix gets read aloud.
- Selectable languages are `translation.supported_languages() ∩ TTS voices` (`application/capabilities.py::_intersection_languages`). A language with Edge voices but no translation support can't be used.
- `build_orchestrator()` always builds the translation provider. A job pinned to `gemini`/`groq`/`openrouter` fails with `PROVIDER_CONFIGURATION_ERROR` when the key is missing, even though it never needs translation.
- Non-UTF-8 `.txt` files (GBK/Big5 Chinese raws) fail with `source text must be UTF-8` (`api/parse.py::_decode_source_text`).
- A failed job shows the raw `error_type: message` (`frontend/src/components/JobProgress.tsx`), for example `PROVIDER_CONFIGURATION_ERROR: …`.

**Narrate mode** adds `mode: "narrate"`. It turns text, `.txt`, or `.srt`/`.vtt` into audio in the **source language**, with no translation step, no LLM tokens and no translation cache. The same release also makes translate mode with `source == target` skip the LLM, adds a pre-submit "analyze" step (encoding, language, chapters, estimate, voice preview), and defines how mixed-language text is handled (§9).

## 2. Goals & non-goals

**Goals**
- `mode: "translate" | "narrate"` on job creation. The default is `translate`, so existing clients and stored jobs keep working.
- Narrate runs parse → chunk → (pass-through) → conservative narration → TTS → normalize → merge. It never calls `TranslationProvider` and never touches translation entries in `PipelineArtifactCache`.
- Translate mode with `source == target` (plain text) also skips the LLM via `_pass_through_chunks` (decision R6).
- **Pre-submit analysis (Phase 1):**
  - global encoding detection, with a decoded preview and the detected encoding;
  - fast language detection that asks the user when confidence is low;
  - a detected chapter list the user can check;
  - an estimate of audio duration and processing time;
  - a ~10 s "Listen" voice preview.
- Voice selection falls back to the same language family by default (R2). **Job creation fails fast** when no voice exists.
- Input normalization: whitespace cleanup and chapter-heading detection via the **shared chapter module from #29**.
- `.srt`/`.vtt` in narrate mode becomes **continuous** narration (Phase 2).
- Works with `TRANSLATION_PROVIDER=fake` and with a missing or misconfigured LLM provider.
- Plain-language error messages in the UI. No internal codes are shown to users (R7).
- **Mixed-language text** is read by one voice. There is no mid-narration voice switching. This is done with single-voice multilingual TTS engines, **VieNeu-TTS v3 Turbo** (local) and **Fish Audio `s2.1-pro-free`** (online), added as new adapters behind the existing `TTSProvider` port. Edge stays the default and the fallback (§9, Phase 3).

**Non-goals (this issue)**
- Timestamp-aligned dubbing for narrate mode (Phase 4; most of the machinery already exists, see §13).
- Per-chapter output files, ZIP and M4B. Those are tracked in **#29**, which replaces the old "Phase 3: per-chapter outputs / M4B" of this issue.
- Raising the upload size limit (**#30**) and making long jobs survive the RQ timeout (**#31**). Both are prerequisites for full-length novels (§12), but they're tracked separately.
- Switching voices per sentence or per language inside one narration (rejected, see R4 in §4).
- LLM text normalization (numbers/dates to words); see open questions.
- Multi-speaker voices, SSML, voice cloning UI, EPUB/PDF/DOCX input.

## 3. User stories
1. As a reader with a Vietnamese `.txt` novel, I pick **Narrate (No model cost)**, upload it, and get Vietnamese audio without configuring any API key.
2. As a user with a GBK-encoded Chinese `.txt`, I see "Detected encoding: GBK" and the first lines decoded correctly **before** I submit. Today the upload fails.
3. As a user, I paste text and leave the language on **Auto**. I see "Detected: Vietnamese (99%)" almost instantly, and the voice list fills in. If detection is unsure (< 70%), I'm asked to pick from the top candidates.
4. As a user, I click **Listen** to hear ~10 s of my text in the selected voice before starting a multi-hour job.
5. As a user, I see the detected chapters (count + titles) and an estimate ("≈ 41 h of audio, ≈ 6–9 h to process") before I submit.
6. As a user who picked the same source and target language in Translate mode, I see the banner "Same language — switch to *Narrate directly*?" and decide myself.
7. As a user, when something fails I read a plain sentence ("The server is being updated, please try again shortly"), not an error code.
8. As a listener of a Vietnamese novel full of English terms, I can choose an engine that reads both languages in **one** voice (VieNeu / Fish, Phase 3).
9. (Phase 2) As a user with an `.srt` transcript, I get one continuous narration with no indices, timestamps or `<i>`/`{\an8}` tags read aloud.
10. As an operator, old jobs in `storage/jobs/*/status.json` and the polling API behave exactly as before.

## 4. Resolved decisions

| # | Topic | Final decision |
|---|---|---|
| R1 | Missing-voice error code | Keep `UNSUPPORTED_LANGUAGE` (HTTP 400). This matches `application/pipeline/tts.py::select_voice`. The API message stays explicit; the UI shows a plain sentence (R7). |
| R2 | Language-family voice fallback | **On by default, also when the user didn't pick a voice.** Exact language first, then voices with the same primary subtag (`en-*`, `es-*`, `pt-*`, `zh-*`), ordered by a preference table. Exception: Cantonese `zh-HK` voices are **not** a fallback for `zh-CN`/`zh-TW` text (Mandarin), and vice versa. Details in §8.3. |
| R3 | Detection preview | **`POST /api/detect-language` ships in Phase 1.** It runs on the first few KB only (≤ 8 KB text sample). If confidence < `LANGUAGE_CONFIRM_THRESHOLD` (default **0.70**), it doesn't auto-select: it returns the top candidates and the UI asks the user. A **Listen** button synthesizes ~10 s of the first sentence with the chosen voice (`POST /api/tts/preview`). |
| R4 | Mixed-language text | **Decided: no mid-narration voice switching** (the earlier "Auto-Voice Router" proposal is rejected because it causes choppy voice changes and a complex timeline). The dominant language picks the voice. Mixed vi/en (and zh) text is handled by **single-voice multilingual engines**: VieNeu-TTS v3 Turbo (local; Vietnamese + English code-switching, no Chinese) and Fish Audio `s2.1-pro-free` (online; automatic per-chunk language detection, vi/en/zh in one voice). Edge stays the default. Engines come in **Phase 3** after a listening pilot (§9). |
| R5 | Encoding detection | **Global, both modes**, in `api/parse.py::_decode_source_text`: BOM → strict UTF-8 → `charset-normalizer` with an allow-list (GB18030/GBK, Big5, Shift-JIS, EUC-KR, CP1258, Windows-1252). The UI shows the detected encoding plus the first decoded lines before submit. |
| R6 | `source == target` | **Backend:** translate mode with `source == target` (explicit, or detected at create) skips the LLM via `_pass_through_chunks` for plain text too, the same as `_run_srt` already does. **Frontend:** show a suggestion banner "Same language — switch to *Narrate directly*?" and **never switch silently**. |
| R7 | Rolling deploy / unknown mode | Missing `mode` → `translate` (no migration). An unknown `mode` → the worker fails fast with a clear message and never translates by accident. **The UI shows plain-language messages only** (for example "The server is being updated, please try again shortly"), mapped from `error_type` (§10.5). |
| R8 | Language lists | Keep both lists in `GET /api/capabilities`: `languages` = translation ∩ TTS (translate mode), and `narrate_languages` = every language with ≥ 1 TTS voice. **When the user switches mode, keep the selected language if it's in the new list**; otherwise fall back to the default. |
| — | Chapter detection | **One shared module** with #29: `application/pipeline/chapters.py::split_chapters`. Narrate uses it from Phase 1; translate adopts it through #29's chapter manifest. |
| — | Cost indicator | Narrate mode is labelled **"No model cost"** (no LLM tokens), in contrast with Translate. In Phase 3 the engine picker shows the engine cost ("Free", "Free · fair use", "Local · CPU"). |

## 5. API changes

### 5.1 `POST /api/jobs` (JSON `CreateJobJsonBody` + multipart `_parse_multipart` in `backend/app/api/parse.py`)

| Field | Type | translate (default) | narrate |
|---|---|---|---|
| `mode` | `"translate" \| "narrate"` | optional, default `translate` | required to opt in |
| `text` / `file` | exactly one | unchanged | unchanged (`.txt` Phase 1, `.srt`/`.vtt` Phase 2) |
| `source_language` | BCP-47 or `"auto"` | required (unchanged) | **optional, default `"auto"`** |
| `target_language` | BCP-47 | required | **optional and ignored.** If sent, it must equal the resolved source, else `INVALID_INPUT` |
| `voice` | string | unchanged | must belong to the resolved source language or its family (R2) |
| `translation_profile_id` | string | unchanged | **rejected** with `INVALID_INPUT` |
| `tts_engine` (Phase 3) | `"edge" \| "vieneu" \| "fish"` | optional, default `TTS_PROVIDER` | same |
| `speed`, `output_format` | unchanged | unchanged | unchanged |

`CreateJobCommand` (`backend/app/application/jobs.py`) gains `mode: JobMode = JobMode.TRANSLATE` (and `tts_engine: str | None` in Phase 3). The response is unchanged: `202 JobCreatedResponse`.

### 5.2 Validation in `JobService.create`
1. `text.strip()` is non-empty (unchanged).
2. **Narrate: resolve the language at creation.** If `source_language == "auto"`, run `LanguageDetector.detect()` on the first ~8 KB after normalization, without chapter headings. Persist the concrete BCP-47 code so the worker doesn't re-detect. Below `LANGUAGE_DETECT_MIN_CONFIDENCE` → `INVALID_INPUT` (existing message). The UI normally prevents this by confirming low-confidence results first (R3).
3. Narrate: `target_language := source_language`. Translate with `source == target`: stored as is, and the worker uses pass-through (R6).
4. **Voice check with family fallback** (R2): `resolve_voice(tts, language, voice)` (§8.3) must return a voice. Otherwise `UNSUPPORTED_LANGUAGE`.
5. Narrate skips all translation-profile `supported_languages` checks.

`JobService.__init__` gains two optional dependencies, `tts: TTSProvider | None` and `detector: LanguageDetector | None`, wired in `config/factory.py::build_job_service`. The API already builds a TTS provider for `/api/capabilities`.

### 5.3 `POST /api/detect-language` (Phase 1)
- Request: JSON `{ "text": "<≤ 8 KB sample>" }`, or multipart `file` holding the **first 64 KB** of the upload (the client uses `Blob.slice`, so a 15 MB book isn't uploaded twice for this call). Bigger bodies get 413.
- Response:
```json
{ "encoding": "gb18030", "preview_lines": ["第一章 少年", "　　…"],
  "language": "zh-CN", "confidence": 0.93, "needs_confirmation": false,
  "candidates": [{"language": "zh-CN", "confidence": 0.93}, {"language": "zh-TW", "confidence": 0.05}],
  "voices": [{"id": "zh-CN-XiaoxiaoNeural", "language": "zh-CN", "label": "…"}] }
```
- `needs_confirmation = confidence < LANGUAGE_CONFIRM_THRESHOLD` (new setting, default `0.70`). `candidates` comes from langdetect's ranked list (`CpuLanguageDetector` already computes `top`; expose the top 3). `preview_lines` holds the first ~10 non-empty decoded lines; `encoding` is only set for file input.
- The frontend calls it debounced (~400 ms) on paste and immediately after a file is picked. It has to be fast (CPU langdetect on ≤ 8 KB in `asyncio.to_thread`, as `CpuLanguageDetector` already does).

### 5.4 `POST /api/analyze` (Phase 1)
- Body: the same shape as `POST /api/jobs` (`mode`, `text`/`file`, languages, `speed`, `voice`). It goes through the same parsing and size checks (#30) and **does not create a job**.
- Response:
```json
{ "encoding": "gb18030", "chars": 4987321,
  "chapters": {"count": 812, "items": [{"index": 1, "title": "第一章 少年", "chars": 6120}, …],
               "truncated": false, "warnings": ["toc_block_skipped"]},
  "estimate": {"audio_seconds": 1036800, "processing_seconds": [21600, 36000],
               "model_cost": "none", "basis": "narrate · vi-VN · edge · speed 1.0"} }
```
- `chapters.items` is capped at 2,000 (`truncated: true` beyond that). It uses `split_chapters` from #29 (§7.3).
- `estimate` (§10.3) is a heuristic with ranges, never a promise. `model_cost` is `"none"` for narrate; in translate mode it gives the translation profile's provider (no price math in Phase 1).
- Cost note: for books this means a second upload (analyze, then create). That's acceptable on the target self-hosted setups; a "draft upload" handle is listed as an open question.

### 5.5 `POST /api/tts/preview` (Phase 1)
- Body: `{ "text": "<≤ 300 chars>", "language": "vi-VN", "voice": "…", "speed": 1.0, "tts_engine": "edge" }`. The response is `audio/mpeg`, about 10 s long.
- The server cuts the text at the first sentence end within 300 chars, then synthesizes through the same `TTSProvider` and `normalize` path as jobs. Results go into `PipelineArtifactCache` (`fill_tts` / `store_tts`), so repeated clicks are free.
- Guarded by a small in-process token bucket (`TTS_PREVIEW_RATE_PER_MINUTE`, default 30) so it can't be used as a free bulk TTS API. Errors use the same friendly mapping.

### 5.6 Error codes (existing `ErrorType`, no new enum values)

| Case | `error_type` | HTTP |
|---|---|---|
| bad `mode` value | `INVALID_INPUT` | 400 |
| `translation_profile_id` with narrate, or `target_language` ≠ source | `INVALID_INPUT` | 400 |
| detection fails or confidence below `LANGUAGE_DETECT_MIN_CONFIDENCE` | `INVALID_INPUT` (`could not detect source language; set source_language explicitly`) | 400 |
| no TTS voice for the language (after family fallback) | `UNSUPPORTED_LANGUAGE` | 400 |
| voice not valid for the language or family | `UNSUPPORTED_LANGUAGE` | 400 |
| undecodable upload | `INVALID_INPUT` (`could not determine text encoding; save the file as UTF-8`) | 400 |
| engine not configured or unavailable (Phase 3) | `PROVIDER_CONFIGURATION_ERROR` | 400 at create (preflight) |

### 5.7 `GET /api/jobs/{id}` (`JobStatusResponse`)
- Adds `mode` (always present; `translate` for old jobs) and `tts_engine` (Phase 3).
- `translation_provider`/`translation_model` stay omitted for narrate jobs (the existing `_omit_absent_pin` serializer).
- `source_language` holds the resolved code.

### 5.8 `GET /api/capabilities`
- Adds `narrate_languages: list[str]`. This needs `languages() -> Sequence[str]` on the `TTSProvider` port (`domain/ports.py`), implemented in `providers/tts/edge.py` (keys of `_voices_by_language`) and `providers/tts/fake.py`.
- Adds `limits: {max_upload_bytes}` (owned by #30) and `estimate_defaults` (chars/sec per language, used by the client-side quick estimate).
- Phase 3 adds `tts_engines: [{id, label, kind: "online"|"local", cost_label, available, languages, max_concurrency}]`, and each voice gets an `engine` field.

## 6. Pipeline changes

### 6.1 Explicit branch in `PipelineOrchestrator.run()` reusing `_pass_through_chunks`
- `_pass_through_chunks` already exists. `_run_srt` uses it when `source == target`. It writes `chunks/<id>.translated.txt` and records `STAGE_TRANSLATED`, so `_narrate_chunks`, `_generate_audio`, `_merge`, checkpoints and resume all work **unchanged**.
- A `PassthroughTranslator` was rejected: it would still go through `translate_chunk()`'s `supported_languages()` check, fill `PipelineArtifactCache.fill_translation/store_translation` with identity "translations", and log the step as a translation.
- The job FSM (`domain/jobs.py::_FORWARD`) is strictly linear. The `TRANSLATING` status is kept and finishes instantly; the UI relabels it (§10.4). No FSM change and no migration.

Sketch (`backend/app/application/pipeline/orchestrator.py`):
```python
job = await self._advance(job, JobStatus.PARSING)
if job.mode is JobMode.NARRATE:
    return await self._run_narrate(job, text, workspace=workspace, checkpoints=checkpoints)
if is_srt_document(text):
    return await self._run_srt(...)
normalized, resolved = await parse_source(text, job.source_language, self._detector)
...
job = await self._advance(job, JobStatus.TRANSLATING)
if job.source_language == job.target_language:          # R6: plain-text same-language bypass
    job = await self._pass_through_chunks(job, chunks, workspace, checkpoints,
        stage=STAGE_TRANSLATED, active_status=JobStatus.TRANSLATING, suffix=".translated.txt")
else:
    job = await self._translate_chunks(job, chunks, workspace, checkpoints)
return await self._finish_audio(job, chunks, workspace, checkpoints)   # shared tail

async def _run_narrate(self, job, text, *, workspace, checkpoints):
    normalized = prepare_narration_text(text)                 # §7.2; Phase 2: SRT → continuous
    spans = split_chapters(normalized)                        # shared with #29 (§7.3)
    chunks = chunk_by_chapter(spans, max_chars=self._max_chars)
    job = replace(job, chunk_total=len(chunks), ...)          # language already resolved at create
    job = await self._advance(job, JobStatus.TRANSLATING)
    job = await self._pass_through_chunks(job, chunks, workspace, checkpoints,
        stage=STAGE_TRANSLATED, active_status=JobStatus.TRANSLATING, suffix=".translated.txt")
    return await self._finish_audio(job, chunks, workspace, checkpoints)
```
`_finish_audio` is the refactored shared tail: `PREPARING_TTS` → `_narrate_chunks` → `GENERATING_AUDIO` → `_generate_audio` → `MERGING` → `_merge` → `COMPLETED`. Translate and narrate (and #29's chapter assembly) hook into it, so the modes don't drift apart.

⚠️ R6 changes behaviour for **in-flight** translate jobs with `source == target` that already have `translated` checkpoints. That's harmless: completed chunks are skipped either way, and the remaining ones are passed through.

### 6.2 Stage-by-stage

| Stage | Module | narrate behaviour |
|---|---|---|
| Parse | `application/pipeline/parse.py` | new `prepare_narration_text()` (§7.2). No detection in the worker. Defensive fallback: if `source_language == "auto"`, call `resolve_source_language` |
| Chapters | new `application/pipeline/chapters.py` (shared with #29) | `split_chapters()` → `ChapterSpan` list; each heading forces a chunk boundary |
| Chunk | `application/pipeline/chunk.py`, `domain/chunking.py` | `chunk_text` per chapter (`CHUNK_MAX_CHARS=1200`, paragraph → sentence → hard split), with global `chunk-NNN` renumbering |
| Translate | `orchestrator._pass_through_chunks` | identity copy; no provider, no cache |
| Narration | `pipeline/narrate.py` + `conservative_narration.py` | unchanged |
| TTS | `pipeline/tts.py`, `orchestrator._generate_audio` | unchanged, except `select_voice` → `resolve_voice` (R2). The TTS cache key (`domain/cache.py::build_cache_key`) already includes provider, model, voice, text, languages and settings, so identical text and voice hit the cache across modes, which is safe |
| Normalize / merge | `pipeline/normalize.py`, `pipeline/merge.py`, `infrastructure/ffmpeg_audio.py` | unchanged (`normalize_argv` resamples to `NORMALIZE_SAMPLE_RATE_HZ=44100` mono, so engines with other sample rates such as 48 kHz VieNeu are fine) |

### 6.3 Worker (`backend/app/workers/runner.py`, `config/factory.py`)
- `build_orchestrator(settings_for_translation_pin(settings, fresh))` always builds the translation provider. For narrate jobs, build a `NullTranslationProvider` instead (new; `providers/translation/null.py`, or next to `PassthroughNarrationProcessor` in `application/pipeline/passthrough.py`). If it's ever called, it raises `DomainError(INTERNAL_ERROR, "translation must not be called in narrate mode")`, so a regression fails loudly in tests.
  - Result: narrate works without `GEMINI_API_KEY`/`GROQ_API_KEY`/`OPENROUTER_API_KEY` and never loads NLLB weights.
- **Unknown `mode`** (R7): `Job.from_dict` parses `mode` leniently. An unrecognized value loads as `JobMode.UNKNOWN`, not a `ValueError`. Otherwise `FilesystemJobStorage.get_job` would raise `STORAGE_FAILED` and the runner would log `worker_job_missing` and leave the job stuck non-terminal. `_process_job_async` marks such a job `FAILED` with `INVALID_INPUT`, `"job requires a newer server version"`.
- `JobService.retry` and `recover_in_progress` need no change, because `mode` is persisted.

## 7. Input parsing

### 7.1 Decoding (R5), `backend/app/api/parse.py::_decode_source_text`, both modes, file uploads
1. UTF-8 BOM / UTF-16 LE/BE BOM.
2. Strict UTF-8.
3. `charset-normalizer` (**new dependency** in `backend/pyproject.toml`) with an allow-list: `gb18030` (superset of GBK/GB2312), `big5`/`big5hkscs`, `shift_jis`, `euc-kr`, `cp1258`, `windows-1252`. Reject when there's no candidate or coherence is low → `INVALID_INPUT` (`could not determine text encoding; save the file as UTF-8`).
4. Existing post-steps stay: reject `\x00`, strip BOM, normalize `\r\n`/`\r` → `\n`.
- Return the detected encoding to the caller, so `/api/detect-language` and `/api/analyze` can report it. Pasted text is already UTF-8.
- Size limits are owned by #30. `max_upload_bytes` applies to raw bytes before decoding.

### 7.2 Whitespace cleanup: `prepare_narration_text` (new, in `application/pipeline/parse.py` or `narrate_text.py`)
- NFC-normalize. Full-width spaces `\u3000` and NBSP become spaces. Strip trailing spaces per line.
- Collapse 3+ blank lines into `\n\n`. In indented-paragraph files (CN novels with `　　` indentation and single newlines), each non-empty line counts as a paragraph.
- Drop separator-only lines (`=====`, `*****`, `------`) and a tiny, documented list of scraper boilerplate.
- `ConservativeNarrationProcessor` still runs afterwards. Its punctuation rules aren't duplicated here.

### 7.3 Chapter headings: **shared module with #29**
- `application/pipeline/chapters.py::split_chapters(text) -> list[ChapterSpan]`. This is **one implementation for both issues**; whichever lands first creates it, and the other reuses it.
- Line-anchored rules (line ≤ 60 chars):
  - `^\s*第[0-9零〇一二两三四五六七八九十百千万]+[章节回卷集部]\s*.*$`
  - `^\s*(Chương|CHƯƠNG|Hồi|HỒI)\s+(\d+|[IVXLCDM]+)\b.*$`
  - `^\s*(Chapter|CHAPTER|Ch\.)\s+(\d+|[IVXLCDM]+|[A-Za-z-]+)\b.*$`
  - plus `Prologue|Epilogue|Lời mở đầu|Phần kết|序章|楔子|尾声`.
- Guards (as in #29 §8.1):
  - skip table-of-contents blocks;
  - handle the preface;
  - collapse duplicate consecutive headings;
  - **fewer than 2 headings → one chapter**.
- Narrate behaviour: each heading becomes its own paragraph, ending in `.` if it has no terminal punctuation (so TTS pauses), and forces a chunk boundary (`chunk_by_chapter`, with global `chunk-NNN` renumbering).
- The chapter list goes into `/api/analyze` (§5.4) and the UI (§10.2). Per-chapter files, the manifest (`chapters.json`), ZIP and M4B are **#29**.
- Translate mode keeps today's chunking until #29's manifest-guarded rollout. That keeps checkpoint ids stable for in-flight jobs.

### 7.4 `.srt` / `.vtt` (Phase 2)
- Existing helpers: `parse.py::strip_srt` (continuous text) and `parse_srt.py::parse_srt_cues` (also strips ASS `{\…}` via `_ASS_TAG_RE`).
- Narrate + SRT means **continuous** narration. Route it to `strip_srt`, **not** `_run_srt`. Fix the gap that `strip_srt` doesn't strip `{\an8}`-style tags (reuse `_ASS_TAG_RE`).
- Join cues without terminal punctuation to the next cue. Break the paragraph on gaps > 2 s (configurable, using `parse_srt_cues` timings).
- Drop sound-effect cues (`[Music]`, `(applause)`, `♪…♪`; configurable list).
- Today `run()` sends **any** SRT to `_run_srt`, so the narrate branch must be checked **before** `is_srt_document` (as in the §6.1 sketch).
- Until Phase 2, `.srt` in narrate gets a 400: "Subtitle narration is coming soon."

## 8. Language detection & voice selection

### 8.1 Detector
- The existing `providers/language_detection/cpu.py::CpuLanguageDetector` (langdetect, seed 0, ISO→BCP-47 `_ISO_TO_BCP47`). No new dependency.
- Expose the top-3 candidates for R3 (today it only returns `top`).
- Sample: normalized text with chapter headings removed, first ≤ 8 KB (R3: speed; and "Chapter 1" headings don't bias the result).

### 8.2 Thresholds
- `LANGUAGE_CONFIRM_THRESHOLD` (new, default `0.70`): below it the UI asks the user (R3).
- `LANGUAGE_DETECT_MIN_CONFIDENCE` (existing, default `0.5`): below it, creating a job with `auto` fails.

### 8.3 Voice resolution with family fallback (R2)
- New `resolve_voice(provider, language, voice)` in `application/pipeline/tts.py` replaces the strict check in `select_voice`. It's used at create time (§5.2), in `_generate_audio` and in preview.
  1. **Exact language:** if `voice` is given and present in `voices_for(language)`, use it. If none is given, use the first entry (it already honours `TTS_DEFAULT_VOICE_BY_LANGUAGE`).
  2. **Same family:** otherwise search `languages()` with the same primary subtag, in a preference order (`FAMILY_PREFERENCE`, e.g. `en: [en-US, en-GB, en-AU, …]`, `pt: [pt-BR, pt-PT]`, `es: [es-ES, es-MX, …]`, `zh-CN: [zh-CN, zh-TW]`, `zh-TW: [zh-TW, zh-CN]`). **Never** fall back across `zh-HK` (Cantonese) ↔ `zh-CN`/`zh-TW` (Mandarin), and skip dialect voices such as `zh-CN-liaoning`/`zh-CN-shaanxi` unless they were chosen explicitly.
  3. Nothing found → `UNSUPPORTED_LANGUAGE`.
- Jobs store the **resolved voice id**, so the worker never re-resolves differently.
- The UI groups voices by family ("English — US / UK / AU …"). Users choose by ear with **Listen** (§5.5).

## 9. TTS engines and mixed-language text (R4, Phase 3)

### 9.1 Decision
- No per-sentence or per-language voice switching inside one narration. Text is read by **one voice**. Mixed-language quality comes from engines that are multilingual inside one voice.
- Engines plug into the existing **`TTSProvider` Protocol** (`domain/ports.py`: `synthesize(text, language, voice, settings) -> AudioArtifact`, `voices_for(language)`), plus the new `languages()` (§5.8). They're registered in `config/factory.py::build_tts_provider`, next to `fake` and `edge` (`providers/tts/edge.py::EdgeTTSProvider`).
- The orchestrator already respects a provider's `max_concurrency` attribute (`getattr(self._tts, "max_concurrency", …)` in `_generate_audio`).

| Engine | Kind | Mixed-language | Cost | Status |
|---|---|---|---|---|
| **Edge** (`edge-tts`) | online, unofficial endpoint | mono-lingual voices: foreign sentences get a heavy accent | free | **default + fallback** (unchanged) |
| **VieNeu-TTS v3 Turbo** ([repo](https://github.com/pnnbao97/VieNeu-TTS), Apache-2.0 code and weights) | local CPU (ONNX) / GPU | Vietnamese + English code-switching in one voice; **no Chinese** | free (your CPU) | Phase 3, opt-in |
| **Fish Audio `s2.1-pro-free`** ([pricing & limits](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits)) | online API | automatic per-chunk language detection; vi/en/zh in one voice (S2.1: 83 languages) | $0 under **fair use** (no published numeric limits); paid `s2.1-pro` is $15 / 1M UTF-8 bytes | Phase 3, opt-in |

### 9.2 Engine selection per job
- Today `TTS_PROVIDER` is **one global setting per deployment** (`config/settings.py`, default `fake`; `.env.example` suggests `edge`). Phase 3 adds an optional per-job pin, `Job.tts_engine`, mirroring the translation pin:
  - stored only when set (like `translation_provider`/`translation_model` in `Job.to_dict`);
  - applied with a new `settings_for_tts_pin(settings, job)` next to `settings_for_translation_pin` in `runner.py`;
  - validated against `TTS_ENGINES_ENABLED` (e.g. `edge,vieneu,fish`).
  - `CapabilitiesService` builds every enabled provider and lists `tts_engines`.
- **Voice ids are namespaced per engine** (`vieneu:<preset>`, `fish:<reference_id>`; Edge ids are unchanged), so a voice can't silently resolve on the wrong engine.

### 9.3 Cache identity
- `PipelineArtifactCache` keys TTS on `CacheIdentity.tts_provider` + `tts_model`. Today `config/factory.py::cache_identity_from_settings` sets **`tts_model = tts` (the provider name)**.
- Change it to an engine-specific model identity, so cached audio can never be replayed across engines, models or quantizations:
  - `edge` → `"edge"`
  - `fish` → `FISH_AUDIO_MODEL` (e.g. `s2.1-pro-free`)
  - `vieneu` → `f"vieneu-v3-turbo:{VIENEU_PRECISION}"`
- The voice (including the Fish `reference_id`) is already in the key. With per-job pinning, the identity is built from the job's pinned engine, not from global settings.

### 9.4 VieNeu-TTS adapter (`providers/tts/vieneu.py`)
- **Integration: sidecar service (recommended).** Run the upstream OpenAI-compatible server (`docker compose -f docker/docker-compose.yml --profile api-cpu`, `POST /v1/audio/speech`, `GET /v1/voices`, `GET /health`) as an optional `vieneu` service in our `docker-compose.yml`. The adapter is a small HTTP client in the style of `providers/translation/openai_compatible.py` (urllib, timeouts).
  - It isolates the ONNX/torch dependencies and the ~15–20 s model load from the worker image.
  - The in-process `vieneu` SDK (`Vieneu(mode="v3turbo", precision=…)`) is the alternative (open question).
- Env:
  - `VIENEU_BASE_URL` (e.g. `http://vieneu:8000/v1`);
  - `VIENEU_MODEL=vieneu-v3-turbo`;
  - `VIENEU_PRECISION=fp32|int8` (sidecar config, also part of the cache identity);
  - `VIENEU_MODEL_DIR` (model cache volume);
  - `VIENEU_HTTP_TIMEOUT_SECONDS` (default 300; long chunks on CPU);
  - `VIENEU_MAX_CONCURRENCY` (default **1**).
- Voices: `GET /v1/voices` (25 presets across North/Central/South) → `Voice(id="vieneu:<name>", language="vi-VN")`. `languages()` returns `["vi-VN"]`. English inside Vietnamese text is handled by the model; English-only text isn't a target.
- Output is wav/pcm at 48 kHz → the existing `normalize` produces mp3 at 44.1 kHz. If the server doesn't honour a speed parameter, apply `AudioProcessor.adjust_tempo`, which already exists.
- ⚠️ **CPU facts from upstream benchmarks (12th-gen i5, 6 P-cores):**
  - fp32 RTF 0.55–0.62;
  - int8 RTF 0.35–0.37, but **int8 requires VNNI** (AVX-512 VNNI / AVX-VNNI) and "on older CPUs int8 can produce garbled audio";
  - two concurrent CPU streams stall (RTF ~1.19).
- **On the owner's i5-10300H (Comet Lake, no AVX-VNNI), int8 is therefore *not* recommended.** Plan for fp32. The earlier estimate of "RTF ~0.5–0.7" is **unbenchmarked**, and on a 4-core 10th-gen CPU it's likely closer to ~0.8–1.2. Measure it in the pilot (§9.7).
- At RTF ≈ 1, a 100 h book is ≈ 100 h of CPU time, so #31 is a hard prerequisite.

### 9.5 Fish Audio adapter (`providers/tts/fish.py`)
- API: `POST https://api.fish.audio/v1/tts`, `Authorization: Bearer $FISH_AUDIO_API_KEY`, header `model: $FISH_AUDIO_MODEL` (default `s2.1-pro-free`; an unknown or missing header falls back to **paid** `s2.1-pro`, so always send it).
  - JSON body: `{text, reference_id, format: "mp3", mp3_bitrate: 128, prosody: {speed}, normalize: true, latency: "normal"}`.
  - **Omit `language`** (per the docs, leave it unset for mixed-language text; it only affects number/date normalization).
- Env:
  - `FISH_AUDIO_API_KEY` (secret, `SecretStr` like `GEMINI_API_KEY`);
  - `FISH_AUDIO_MODEL`;
  - `FISH_AUDIO_BASE_URL` (default `https://api.fish.audio`);
  - `FISH_AUDIO_VOICES`, e.g. `vi-VN=<reference_id>:Ngọc Lan,en-US=<reference_id>:Narrator`, parsed like `parse_tts_default_voice_by_language`. A reference voice may be listed under several languages, because the model is multilingual;
  - `FISH_AUDIO_HTTP_TIMEOUT_SECONDS` (default 120);
  - `FISH_AUDIO_MAX_CONCURRENCY` (default **2**; the account-wide Starter tier allows 5 concurrent requests shared by all keys).
- Cost transparency: Fish bills per UTF-8 byte. Vietnamese is ~1.3–1.5 bytes/char (diacritics), so ≈ 7 MB of text per 100 h of audio, which would be **≈ $105 per 100 h on paid `s2.1-pro`**. Fish's own note: 1M bytes ≈ 12 h of English speech. On `s2.1-pro-free` it's $0, but a full novel is far beyond "testing/prototyping", so **fair-use throttling or refusal is a real risk**.

### 9.6 Rate limits, 429 and fallback (all engines)
- Error mapping in the adapters:

| Response | Mapped to |
|---|---|
| `429` | `DomainError(PROVIDER_RATE_LIMIT, retry_after_seconds=…)` |
| `503` / connection error | `TTS_FAILED` (retryable) |
| timeout | `TIMEOUT` |
| `401` / `402` | `PROVIDER_CONFIGURATION_ERROR` (not retryable) |
| sidecar down at preflight | `PROVIDER_CONFIGURATION_ERROR` |

- `Retry-After`: move `_parse_retry_after` / `_retry_after_header` out of `providers/translation/openai_compatible.py` into a shared helper (e.g. `providers/http_retry.py`). Fish documents that its 429s come **without** `Retry-After`, so we fall back to exponential backoff.
- Retry policy: `with_chunk_retry` (`application/pipeline/retry.py`) already honours `retry_after_seconds` and retries `PROVIDER_RATE_LIMIT` (`domain/retry.py::RETRYABLE_ERROR_TYPES`). But `RETRY_MAX_ATTEMPTS=3` with `RETRY_BACKOFF_SECONDS=1` gives up after ~3 s. Add rate-limit-specific `RETRY_RATE_LIMIT_MAX_ATTEMPTS` (default 6) and `RETRY_RATE_LIMIT_BACKOFF_SECONDS` (default 5, capped at 120 s per wait).
- **Fallback to Edge is never silent mid-book.** Switching engines changes the voice.
  - (a) **Preflight at create:** if the pinned engine is unavailable (missing key, sidecar `GET /health` fails), the 400 response carries a friendly message, and the UI offers "Use Edge instead".
  - (b) **During a job:** once retries are exhausted the job (or, with #29, the chapter) fails with a friendly message ("Fish Audio is limiting free usage right now. Try again later or switch to Edge."). With #31's continuation the job resumes later from checkpoints.
  - (c) **Explicit switch:** "Continue with Edge" re-pins the remaining work. With #29 that's per chapter and recorded in the chapter manifest, so mixed-engine books are visible, never hidden.
- Optional `TTS_FALLBACK_ENGINE=edge` with `TTS_FALLBACK_SCOPE=off|chapter` (default `off`).

### 9.7 Listening-quality pilot (required before changing any default)
- [ ] Pick one representative chapter of a translated CN→VN novel: Sino-Vietnamese names, English terms, numbers and dates, `[ ]`/`【】` system-message brackets, dialogue.
- [ ] Synthesize it with Edge (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`), VieNeu v3 Turbo **fp32** (and int8 only on a VNNI CPU), and Fish `s2.1-pro-free` with 1–2 reference voices.
- [ ] Do a blind A/B listen by the owner. Score names, English code-switching, numbers, prosody and long-form consistency, and artifacts (garbling, repetition, early stop).
- [ ] Measure wall time / RTF on the real host (i5-10300H), 429/error counts, and estimated cost per 100 h.
- [ ] Record the results in `docs/adr/0012-multilingual-tts-engines.md` and decide the defaults per language (e.g. vi-VN → VieNeu if quality ≥ Edge and RTF is acceptable). **Edge stays the default until this is done.**
- **Bracket sanitizing (found during spec):** Fish S2 treats any `[bracket]` text as an emotion/prosody cue, and VieNeu treats `[cười]`-style tags as cues. Web novels often contain `[…]`/`【…】` system messages. Adapters get a `prepare_text()` hook that rewrites brackets to neutral quotes or parentheses **only for those engines**, and the pilot verifies it.

## 10. Frontend (`frontend/src`)

### 10.1 Mode and form (`components/JobForm.tsx`, `api/types.ts`, `api/client.ts`)
- Segmented control: **Translate & narrate** / **Narrate only — No model cost**. Translate shows the profile's provider and model as its cost source.
- `api/types.ts`: `JobMode = "translate" | "narrate"`; add `mode` to `CreateJobInput` and `JobStatusResponse`; add `narrate_languages`, `limits`, `estimate_defaults` (and Phase 3 `tts_engines`) to `Capabilities`.
- `api/client.ts::createJob` sends `mode` in both the JSON and the `FormData` branch. Narrate omits `target_language` and `translation_profile_id`. New calls: `detectLanguage`, `analyze`, `ttsPreview`.
- Narrate hides the target-language select and the translation-profile row. The source select lists `narrate_languages` plus **Auto**.
- **Switching mode keeps the selected source/target language if it exists in the new list** (R8). Otherwise it falls back to `resolveSourceDefault`/`resolveTargetDefault` (`lib/jobUi.ts`). Persist the last mode in `localStorage`.
- **Same-language banner (R6):** in Translate mode, when `source == target` (or detected source == target), show "Same language — switch to *Narrate directly*?" with a **Switch** button. Never switch automatically.
- `lib/jobUi.ts::canGenerate`: narrate doesn't require `targetLanguage`, and is blocked while detection `needs_confirmation`.

### 10.2 Pre-submit analysis panel
- On paste (debounced) or file pick: call `detectLanguage` and show:
  - "Detected: Vietnamese (99%)";
  - for files, "Encoding: GBK" plus the **first ~10 decoded lines** in a monospace box (catches mojibake before a multi-hour job);
  - below 70%, a "Which language is this?" chooser with the top candidates.
- **Listen** button next to the voice select: plays a ~10 s preview of the first sentence (`ttsPreview`), with loading and error states.
- **Chapters:** "812 chapters detected" with an expandable, windowed list (index, title, length). Warnings such as "table of contents skipped" or "no chapters found — whole text is one chapter".
- **Estimate:** "≈ 288 h of audio · ≈ 2–4 days to process · No model cost" (§10.3). Long estimates show an info note linking the prerequisites (#30/#31) when they aren't deployed yet.
- `.srt` in narrate before Phase 2: inline warning "Subtitle narration is coming soon".

### 10.3 Estimate heuristic
- Audio seconds ≈ `target_chars / (chars_per_second[lang] × speed)`. Starting constants live in `ESTIMATE_CHARS_PER_SECOND` (settings, exposed via capabilities): `vi-VN≈14`, `en-US≈15`, `zh-CN≈4.5`, `ja-JP≈6`. Translate zh→vi multiplies chars by an expansion factor (`ESTIMATE_TRANSLATION_EXPANSION`, start ≈ 3.0).
  - Calibrate from completed jobs (merged duration via `AudioProcessor.get_duration_ms` vs chars, logged per language and voice).
  - ⚠️ With these constants, ~5M Chinese chars come out at **~250–300 h** of Vietnamese audio, several times the 50–100 h figure used in #29/#31. Validate in the pilot. It matters for disk (#29) and runtime (#31).
- Processing seconds ≈ `audio_seconds × engine_RTF / effective_tts_concurrency`, plus translation time for translate mode (`chunks × avg_latency / CHUNK_TRANSLATION_CONCURRENCY`). Show it as a range (×0.7–×1.5).
- `lib/jobUi.ts::estimateTextMetrics` already counts chars and words and estimates chunks; the instant client-side estimate builds on it, and `/api/analyze` returns the authoritative one.

### 10.4 Progress
- `components/JobProgress.tsx` / `lib/jobUi.ts::progressCopy`: in narrate (and the same-language bypass) the `translating` step reads "Preparing text" (`PIPELINE_STEPS` label).

### 10.5 Plain-language errors (R7)
- Today `JobProgress.tsx` renders `{job.error_type}: {job.message}` verbatim. Replace that with `friendlyError(error_type, message)` in `lib/jobUi.ts`, which maps every `ErrorType` to a sentence (e.g. `INVALID_INPUT` + "job requires a newer server version" → "The server is being updated, please try again shortly."; `PROVIDER_RATE_LIMIT` → "The voice service is busy. We'll keep retrying; you can also try again later.").
- The raw code and message stay available under a collapsed "Technical details" toggle for bug reports, never as the headline. The same mapping is used for form errors (`ApiError` from `api/client.ts::parseError`).

### 10.6 Tests
- `JobForm.test.tsx`, `client.test.ts`, `jobUi.test.ts`, `JobProgress.test.tsx` (§15).

## 11. Data model, migration & backward compatibility
- `backend/app/domain/jobs.py`: add `class JobMode(StrEnum): TRANSLATE="translate"; NARRATE="narrate"; UNKNOWN="unknown"` and `Job.mode: JobMode = JobMode.TRANSLATE`.
  - `to_dict` writes `"mode"` **only for narrate**, mirroring how `translation_*` and `recovery_attempts` are written only when set. Old snapshots and fixtures stay byte-identical.
  - `from_dict`: missing → `TRANSLATE`; unknown → `UNKNOWN` (R7, §6.3). No migration.
  - `__post_init__`: a narrate job has no translation pin and `target_language == source_language`.
- Phase 3: `Job.tts_engine: str | None = None`, written only when set, with the same pattern.
- The Redis job store (`infrastructure/redis_job_store.py`) and the FS/dual-write store (`infrastructure/job_store.py::DualWriteJobStore`) go through `to_dict`/`from_dict`. Nothing else changes.
- Rolling deploy: an **old** worker that predates this change will ignore `mode` and translate `src→src`. Deploy worker before API (or restart both; single-host Compose makes this moot) and note it in the release notes. The `UNKNOWN` guard protects every future mode addition.
- Existing translate behaviour is unchanged, except the same-language bypass (R6), including SRT and timed SRT.

## 12. Prerequisites & related issues
- **#29 (per-chapter audio download):** owns `chapters.json`, per-chapter files, ZIP/M4B and per-chapter retry. **Narrate reuses its shared chapter module** (§7.3) and, once #29 Phase 1 lands, its per-chapter outputs for chaptered input.
- **#30 (upload limit):** **a blocker for full-length novels.** With the default `MAX_UPLOAD_BYTES=2_000_000`, a ~15 MB novel can't be uploaded (backend, `.env.example`, Compose, `frontend/src/lib/constants.ts`, nginx `client_max_body_size 3m`). It also supplies `limits.max_upload_bytes` to the UI.
- **#31 (long-job timeout):** **a blocker for full-length novels.** `RQ_JOB_TIMEOUT_SECONDS=1800` turns multi-hour jobs into repeated `FAILED`/`TIMEOUT` plus manual retries. CPU engines (VieNeu at RTF ~1) make this worse.
- Narrate Phase 1 can ship **before** #30/#31 for short and medium texts. "Full novel" acceptance (§14) requires them.

## 13. Implementation plan

### Phase 1: Narrate MVP + pre-submit analysis (Edge only)
Backend
- [ ] `domain/jobs.py`: `JobMode` (+`UNKNOWN`), `Job.mode`, `to_dict`/`from_dict`/`__post_init__` (§11).
- [ ] `api/schemas.py` + `api/parse.py`: `mode` in JSON and multipart; `JobStatusResponse.mode`.
- [ ] `application/jobs.py`: `CreateJobCommand.mode`; narrate validation (§5.2); inject `tts` and `detector` via `config/factory.py::build_job_service`.
- [ ] `api/parse.py::_decode_source_text`: BOM / UTF-8 / `charset-normalizer` allow-list; return the encoding (R5); add the dependency to `pyproject.toml`.
- [ ] `application/pipeline/parse.py`: `prepare_narration_text` (§7.2).
- [ ] `application/pipeline/chapters.py`: `split_chapters` + `chunk_by_chapter`, **shared with #29** (§7.3).
- [ ] `orchestrator.py`: `_run_narrate`, shared `_finish_audio` tail, same-language bypass for plain text (R6), narrate check before `is_srt_document`.
- [ ] `NullTranslationProvider` + narrate wiring in `runner.py` / `factory.py`; worker handling for `JobMode.UNKNOWN` (§6.3).
- [ ] `TTSProvider.languages()` (Edge, fake); `resolve_voice` with family preference and the `zh-HK` exclusion (§8.3).
- [ ] `CpuLanguageDetector`: top-3 candidates; settings `LANGUAGE_CONFIRM_THRESHOLD`, `TTS_PREVIEW_RATE_PER_MINUTE`, `ESTIMATE_CHARS_PER_SECOND`, `ESTIMATE_TRANSLATION_EXPANSION` (+ `.env.example`, `test_env_example.py`).
- [ ] Endpoints `POST /api/detect-language`, `POST /api/analyze`, `POST /api/tts/preview` (§5.3–5.5); capabilities `narrate_languages`, `estimate_defaults`.
- [ ] `.srt` + narrate → 400 "coming soon".

Frontend
- [ ] Mode toggle with the "No model cost" label; keep the language across mode switches; same-language banner (§10.1).
- [ ] Analysis panel: detection with confirmation below 70 %, encoding + decoded preview, chapter list, estimate, Listen button (§10.2–10.3).
- [ ] `canGenerate` and progress relabel; `friendlyError` mapping with "Technical details" (§10.4–10.5).

Docs
- [ ] README API section (new endpoints, `mode`); ADR addendum to `docs/adr/0005-translation-narration-tts-separation.md`; spec copied to `docs/superpowers/specs/`.

### Phase 2: Subtitles + chapter outputs
- [ ] `.srt`/`.vtt` continuous narration: `strip_srt` + `_ASS_TAG_RE`, cue→sentence merge, gap paragraphs, SFX-cue drop (§7.4).
- [ ] Narrate uses #29 Phase 1 per-chapter assembly / ZIP for chaptered input (no extra work here beyond wiring `_finish_audio` to #29's hooks), plus an optional chapter pause (`AudioProcessor.generate_silence`) between chapters in merged output.
- [ ] (Optional) Pull the narrate + SRT timestamp-aligned flag forward from Phase 4 if it's cheap.

### Phase 3: Multilingual single-voice TTS engines (R4)
- [ ] Engine pinning: `Job.tts_engine`, `settings_for_tts_pin`, `TTS_ENGINES_ENABLED`, capabilities `tts_engines`, namespaced voice ids (§9.2).
- [ ] Cache identity per engine/model/precision in `cache_identity_from_settings` (§9.3).
- [ ] Shared `Retry-After` helper extracted from `openai_compatible.py`; rate-limit retry settings (§9.6).
- [ ] `providers/tts/vieneu.py` + optional `vieneu` Compose service (sidecar), `VIENEU_*` env, `max_concurrency=1` (§9.4).
- [ ] `providers/tts/fish.py`, `FISH_AUDIO_*` env, `model` header always sent, 401/402/429/503 mapping (§9.5).
- [ ] Bracket-sanitizing `prepare_text()` hook for Fish/VieNeu.
- [ ] Preflight availability check at create; explicit "Use/Continue with Edge" flows; optional `TTS_FALLBACK_*` (§9.6).
- [ ] UI engine picker with cost labels ("Free", "Free · fair use", "Local · CPU").
- [ ] **Listening pilot** + ADR 0012 decides the defaults (§9.7). No default change before that.

### Phase 4: Later
- [ ] Narrate + SRT **timestamp-aligned** dubbing: route to `_run_srt` + `merge_srt_cues` + `timeline.schedule_srt_timeline` with the pass-through branch (already exists), behind `srt_timing: "continuous" | "aligned"`.
- [ ] Optional LLM text normalization (numbers/dates/abbreviations → words, no translation) as a `NarrationProcessor` with its own cache operation. Fish `normalize: true` already covers part of this for Fish.

## 14. Acceptance criteria

**Phase 1**
- [ ] `POST /api/jobs` accepts `mode` (JSON and multipart). Omitted means `translate`; existing API tests pass unchanged.
- [ ] Narrate with pasted text or `.txt` produces source-language audio. `TranslationProvider.translate` is never called and no translation cache entries are created.
- [ ] Narrate works with `TRANSLATION_PROVIDER=fake` (no `[xx-XX]` prefix read aloud) and with an unconfigured LLM provider.
- [ ] Translate with `source == target` on plain text makes **zero** translation calls (R6). The UI shows the switch banner and never switches by itself.
- [ ] GBK/GB18030, Big5 and UTF-16 `.txt` decode correctly in both modes. The UI shows the detected encoding and the first decoded lines before submit. Undecodable files get a clear message.
- [ ] `POST /api/detect-language` answers in < 300 ms for an 8 KB sample. Confidence < 0.70 → the UI asks the user and doesn't auto-select.
- [ ] Voice family fallback works without a manual pick (`en-US` text → `en-GB` voice when no `en-US` voice exists), and never maps Mandarin ↔ Cantonese.
- [ ] Listen plays a ~10 s preview with the selected voice; repeated clicks hit the cache; the endpoint is rate-limited.
- [ ] The chapter list (count + titles) and the duration/processing estimate are shown before submit. Narrate is labelled "No model cost".
- [ ] Switching mode keeps the selected language when it's available in the new list.
- [ ] Chapter headings start a new chunk and get a pause in narrate. Translate-mode chunking is unchanged.
- [ ] `GET /api/capabilities` returns `narrate_languages`; `GET /api/jobs/{id}` returns `mode`.
- [ ] Old `status.json` without `mode` loads as `translate`; an unknown `mode` fails the job with a plain-language UI message (no internal code in the headline).
- [ ] Retry and crash recovery of narrate jobs resume from checkpoints.
- [ ] CI (`.github/workflows/ci.yml`) is green; docs are updated.

**Full-length novel (needs #30 + #31)**
- [ ] A ~15 MB / 800+ chapter Chinese or Vietnamese `.txt` uploads, is analyzed (encoding, chapters, estimate) and completes unattended in narrate mode.

**Phase 2**
- [ ] `.srt`/`.vtt` narrate produces continuous audio with no indices, timecodes or `<i>`/`{\an8}` tags read aloud, and SFX cues dropped.

**Phase 3**
- [ ] Engines can be selected per job; the cache never mixes engines, models or precisions; 429s back off and resume; nothing switches to Edge without the user's action; the pilot results are recorded in ADR 0012 before any default changes.

## 15. Testing plan

**Unit (`backend/tests`)**
- `domain/test_job_record.py`:
  - `mode` round-trip; missing → `translate`; unknown → `UNKNOWN`; `to_dict` omits `mode` for translate;
  - narrate with a pin, or with target ≠ source, raises;
  - `tts_engine` round-trip (Phase 3).
- `application/test_create_job.py`:
  - narrate + auto detection persists the resolved language;
  - no voice / wrong voice → `UNSUPPORTED_LANGUAGE`; family fallback picks `en-GB`; `zh-HK` is never chosen for `zh-CN`;
  - `translation_profile_id` → `INVALID_INPUT`; mismatched target → `INVALID_INPUT`;
  - the translate path is unchanged.
- New `application/test_narrate_text.py`: whitespace cleanup, `　　` indentation, separator lines.
- New `application/test_chapters.py` (shared with #29):
  - CN/VN/EN headings;
  - negatives (mid-line "the chapter 3 of…");
  - TOC skip, preface, duplicates, < 2 headings → one chapter;
  - chunk boundaries with global renumbering.
- `api/test_jobs.py` + parse tests:
  - GBK, Big5 and UTF-16 BOM decode, and the encoding is returned; garbage → `INVALID_INPUT`;
  - invalid `mode` → 400; multipart and JSON parity;
  - `/api/detect-language` (`needs_confirmation`, candidates, `preview_lines`, 64 KB slice);
  - `/api/analyze` (chapters capped, estimate fields, no job created);
  - `/api/tts/preview` (≤ 300 chars, cache hit, rate limit).
- `application/test_pipeline.py`:
  - narrate with `FakeTranslationProvider(error=AssertionError)` / `NullTranslationProvider` completes, `translation.calls == []`, no translation cache entries, `translated` pass-through checkpoints, resume mid-TTS;
  - **translate `source == target` → zero translation calls**.
- `providers/test_edge.py` / fake TTS: `languages()`.
- `api/test_capabilities.py`: `narrate_languages` ⊇ `languages`; `estimate_defaults`.
- Phase 2: `test_parse_srt.py` and new continuous-narration tests (index, timecode, `<i>`, `<font>`, `{\an8}`, `{\i1}`, multi-line merge, `[Music]`, VTT header).
- Phase 3:
  - `providers/test_fish.py` / `providers/test_vieneu.py`, with HTTP faked like the existing `test_openai_compatible_translation.py`: request shape (the `model` header is always sent, no `language` for mixed text), voice parsing, 401/402 → `PROVIDER_CONFIGURATION_ERROR`, 429 with/without `Retry-After` → `PROVIDER_RATE_LIMIT`, 503 → `TTS_FAILED`, timeout → `TIMEOUT`, bracket sanitizing;
  - `config/test_factory.py`: engine registry, `settings_for_tts_pin`, per-engine cache identity (`test_cache_keys.py`);
  - `domain/test_retry.py`: rate-limit backoff settings.

**Integration**
- `workers/test_process_job.py`: narrate end to end with `TRANSLATION_PROVIDER=fake`, `TTS_PROVIDER=fake` (output has no `"[vi-VN]"` prefix). Narrate with `TRANSLATION_PROVIDER=gemini` and **no** key completes. Unknown-mode job → `FAILED` with the friendly-mappable message.
- Frontend (Vitest):
  - mode toggle and language retention; same-language banner;
  - detection confirmation flow; encoding preview; chapter list; estimate; Listen button states;
  - `friendlyError` mapping; payload shape; `canGenerate`.

**Fixtures (`backend/tests/fixtures/`)**
- `narrate-zh-CN-gbk.txt` (GBK, `第一章` headings, `　　` indents), `narrate-zh-TW-big5.txt`, `narrate-vi-VN-chapters.txt`, `narrate-en-US-chapters.txt`, `narrate-mixed-vi-en.txt` (pilot/regression text with English terms and `[System]` brackets), `narrate-tags.srt`, `narrate-utf16.txt`.
- Mark them binary in `.gitattributes`, and update `tests/test_sample_fixtures.py` if it enumerates fixtures.

**Manual / pilot**
- §9.7 listening pilot.
- A full-novel dry run on the Compose stack once #30/#31 are deployed.

## 16. Open questions (remaining)
1. **Analyze upload cost:** is uploading the book twice (analyze, then create) acceptable, or should `/api/analyze` return a short-lived `draft_id` that `POST /api/jobs` can reference? This depends on #30's size decisions.
2. **VieNeu integration:** sidecar (recommended) or the in-process `vieneu` SDK in the worker image? This needs the SDK's Python and ONNX Runtime compatibility with our `requires-python >=3.12` image and the CPU image size.
3. **Fish fair use:** is `s2.1-pro-free` acceptable for full novels at all, or should Fish be limited to previews/pilots unless a paid key (`s2.1-pro`, ≈ $105 per 100 h of Vietnamese audio) is configured?
4. **Estimate calibration:** where do the measured chars/sec per voice live (a static table in settings vs learned from completed jobs)? Also confirm the zh→vi expansion factor with the pilot, given the ~250–300 h projection for 5M chars.
5. **Engine per chapter:** with #29, may a book legitimately mix engines after "Continue with Edge", or must the whole book be regenerated for consistency?

**Resolved (kept for traceability; see §4):**
- ~~Missing-voice error code~~ → R1.
- ~~Language-family fallback~~ → R2.
- ~~Detection preview endpoint~~ → R3.
- ~~Mixed-language text~~ → R4.
- ~~Encoding detection in translate mode~~ → R5.
- ~~Auto narrate when source == target~~ → R6.
- ~~Rolling deploy order~~ → R7.
- ~~`narrate_languages` vs `languages`~~ → R8.
