# Narrate Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Narrate mode (`mode: "narrate"`) to convert text/TXT directly into audiobooks in the source language without translation or LLM token costs, skip LLM calls on plain text when `source == target`, add pre-submit analysis (`/api/detect-language`, `/api/analyze`, `/api/tts/preview`), support multi-encoding raw files (GBK/Big5), voice-family fallbacks, and user-friendly error messages.

**Architecture:** Extend domain `Job` with `JobMode` enum (`translate`, `narrate`, `unknown`). Re-use the existing pipeline pass-through mechanism (`_pass_through_chunks`) in `PipelineOrchestrator._run_narrate` and in same-language Translate mode to record checkpoint artifacts without LLM calls. Add robust global charset detection in API parsing, chapter splitting with regex heuristics in pipeline, pre-submit analysis endpoints, and integrate a mode toggle, analysis panel, and friendly error display into the frontend.

**Tech Stack:** FastAPI, Pydantic, Python 3.12+, `charset-normalizer`, Edge TTS, React 18, TypeScript, Tailwind CSS, Vitest, Pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-narrate-mode-design.md`

## Global Constraints

- Never hard-code source or target languages in business logic; use `source_language`, `target_language`, and provider capabilities.
- Domain code calls `TranslationProvider` and `TTSProvider` ports; never vendor APIs directly.
- Default `mode` is `"translate"` so existing clients and persisted jobs remain 100% backward compatible.
- Narrate mode must never call `TranslationProvider.translate` and never create translation cache entries.
- Missing LLM credentials must never block Narrate jobs.
- Voice resolution must fall back to the same language family (`en-US` -> `en-GB`), with the strict exception that Cantonese `zh-HK` voices are never used for Mandarin `zh-CN`/`zh-TW` and vice versa.
- SRT narrate mode returns 400 "coming soon" in Phase 1 (reserved for Phase 2).
- Plain-language error messages in frontend; internal error codes must only appear under expandable technical details.

## Review Focus

1. Non-UTF-8 files (GBK, Big5, UTF-16 BOM) with invalid bytes or non-text payloads must fail gracefully with `INVALID_INPUT` and clear messages.
2. Voice resolution for unsupported or mismatched languages must fail fast at job creation with `UNSUPPORTED_LANGUAGE`.
3. Auto-detection on short or ambiguous text (< 70% confidence) must flag `needs_confirmation=True` and offer top candidates.
4. Narrate jobs resuming from intermediate checkpoints must resume cleanly without invoking translation.
5. Translate mode with identical source and target languages must produce identical audio to standard translation while executing zero LLM calls.

---

### Task 1: Domain & Model Foundation (`JobMode`, `Job`, `NullTranslationProvider`)

**Files:**
- Modify: `backend/app/domain/jobs.py`
- Modify: `backend/app/domain/ports.py`
- Modify: `backend/tests/domain/test_job_record.py`

**Interfaces:**
- Consumes: `app.domain.jobs.Job`, `app.domain.ports.TranslationProvider`
- Produces:
  - `JobMode(StrEnum)`: `TRANSLATE = "translate"`, `NARRATE = "narrate"`, `UNKNOWN = "unknown"`
  - `Job.mode: JobMode = JobMode.TRANSLATE`
  - `NullTranslationProvider(TranslationProvider)` in `backend/app/domain/ports.py`

- [ ] **Step 1: Write the failing test**

In `backend/tests/domain/test_job_record.py`:
```python
def test_job_mode_serialization_and_validation():
    from app.domain.jobs import Job, JobMode, OutputFormat

    job = Job(
        job_id="test-1",
        source_language="vi-VN",
        target_language="vi-VN",
        output_format=OutputFormat.MP3,
        mode=JobMode.NARRATE,
    )
    d = job.to_dict()
    assert d["mode"] == "narrate"

    restored = Job.from_dict(d)
    assert restored.mode == JobMode.NARRATE

    # Default fallback for missing mode
    d_no_mode = d.copy()
    del d_no_mode["mode"]
    assert Job.from_dict(d_no_mode).mode == JobMode.TRANSLATE

    # Unknown mode fallback
    d_invalid = d.copy()
    d_invalid["mode"] = "unsupported_mode"
    assert Job.from_dict(d_invalid).mode == JobMode.UNKNOWN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/domain/test_job_record.py -k test_job_mode -v`
Expected: FAIL with `ImportError: cannot import name 'JobMode'` or `unexpected keyword argument 'mode'`

- [ ] **Step 3: Implement `JobMode` and `NullTranslationProvider`**

In `backend/app/domain/jobs.py`:
- Define `JobMode(StrEnum)` with `TRANSLATE = "translate"`, `NARRATE = "narrate"`, `UNKNOWN = "unknown"`.
- Add `mode: JobMode = JobMode.TRANSLATE` to `Job`.
- Update `to_dict` to include `self.mode.value`.
- Update `from_dict` to read `raw_mode = data.get("mode", JobMode.TRANSLATE.value)` and safely parse into `JobMode` (falling back to `JobMode.UNKNOWN` on `ValueError`).
- In `Job.__post_init__`, assert that if `mode == JobMode.NARRATE`, `translation_profile_id is None`.

In `backend/app/domain/ports.py`:
- Add `NullTranslationProvider(TranslationProvider)` with `languages() -> set()` returning empty set and `translate()` raising `DomainError(ErrorType.PROVIDER_CONFIGURATION_ERROR, "translation is not supported in narrate mode")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/pytest backend/tests/domain/test_job_record.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/jobs.py backend/app/domain/ports.py backend/tests/domain/test_job_record.py
git commit -m "feat(domain): add JobMode and NullTranslationProvider"
```

---

### Task 2: Global Encoding Detection (`charset-normalizer`, BOM, UTF-8/GBK/Big5)

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/app/api/parse.py`
- Modify: `backend/tests/api/test_jobs.py`

**Interfaces:**
- Consumes: raw file bytes
- Produces: `_decode_source_text(raw: bytes) -> tuple[str, str]` returning `(decoded_text, detected_encoding)`

- [ ] **Step 1: Write the failing test**

In `backend/tests/api/test_jobs.py`:
```python
def test_decode_source_text_encodings():
    from app.api.parse import _decode_source_text

    # UTF-8 with BOM
    text_utf8, enc = _decode_source_text(b"\xef\xbb\xbfHello world")
    assert text_utf8 == "Hello world"
    assert "utf-8" in enc.lower()

    # GBK Chinese
    sample_gbk = "第一章 序幕".encode("gbk")
    text_gbk, enc_gbk = _decode_source_text(sample_gbk)
    assert text_gbk == "第一章 序幕"
    assert "gb" in enc_gbk.lower()

    # Big5 Traditional Chinese
    sample_big5 = "第一章 序幕".encode("big5")
    text_big5, enc_big5 = _decode_source_text(sample_big5)
    assert text_big5 == "第一章 序幕"
    assert "big5" in enc_big5.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/api/test_jobs.py -k test_decode_source_text_encodings -v`
Expected: FAIL with `too many values to unpack` or decoding error

- [ ] **Step 3: Implement `_decode_source_text` with `charset-normalizer`**

In `backend/pyproject.toml`:
Add `"charset-normalizer>=3.4,<4"` to `dependencies`. Install into `.venv`.

In `backend/app/api/parse.py`:
- Implement `_decode_source_text(raw: bytes) -> tuple[str, str]`:
  1. Check UTF-8 BOM (`\xef\xbb\xbf` -> strip and decode utf-8, return `"utf-8-sig"`).
  2. Check UTF-16 LE/BE BOMs (`\xff\xfe`, `\xfe\xff` -> decode utf-16, return `"utf-16"`).
  3. Try `raw.decode("utf-8")` -> return `(text, "utf-8")`.
  4. Use `charset_normalizer.from_bytes(raw).best()`. If match found with encoding in allow-list (`{"gb18030", "gbk", "gb2312", "big5", "shift_jis", "euc-kr", "cp1258", "windows-1252", "latin-1"}`), decode and return `(text, match.encoding)`.
  5. If undecodable, raise `DomainError(ErrorType.INVALID_INPUT, "source text has unsupported encoding")`.
- Update all internal usages of `_decode_source_text` in `parse.py` to take the first element `text` or handle `(text, encoding)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/pytest backend/tests/api/test_jobs.py -k test_decode_source_text_encodings -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/app/api/parse.py backend/tests/api/test_jobs.py
git commit -m "feat(api): add multi-encoding detection with charset-normalizer"
```

---

### Task 3: Text Normalization & Chapter Splitting (`prepare_narration_text`, `split_chapters`)

**Files:**
- Modify: `backend/app/application/pipeline/parse.py`
- Create: `backend/app/application/pipeline/chapters.py`
- Create: `backend/tests/application/test_narrate_text.py`
- Create: `backend/tests/application/test_chapters.py`

**Interfaces:**
- Consumes: raw source text
- Produces:
  - `prepare_narration_text(text: str) -> str`
  - `Chapter(index: int, title: str, body: str, char_count: int)`
  - `split_chapters(text: str) -> list[Chapter]`
  - `chunk_by_chapter(chapters: list[Chapter], max_chars: int) -> list[Chunk]`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/application/test_narrate_text.py`:
```python
def test_prepare_narration_text_cleanup():
    from app.application.pipeline.parse import prepare_narration_text

    raw = "　　Line 1   \r\n\r\n* * *\r\n\r\n\r\nLine 2\n---\nLine 3"
    cleaned = prepare_narration_text(raw)
    assert "　　" not in cleaned
    assert "* * *" not in cleaned
    assert "---" not in cleaned
    assert "\r" not in cleaned
    assert "\n\n\n" not in cleaned
```

In `backend/tests/application/test_chapters.py`:
```python
def test_split_chapters_multilingual():
    from app.application.pipeline.chapters import split_chapters

    text_cn = "序言\n内容\n第一章 启程\n内容一\n第二章 远行\n内容二"
    chapters = split_chapters(text_cn)
    assert len(chapters) == 2
    assert chapters[0].title == "第一章 启程"
    assert chapters[1].title == "第二章 远行"

    text_vn = "Chương 1: Mở đầu\nNội dung 1\nChương 2: Diễn biến\nNội dung 2"
    chapters_vn = split_chapters(text_vn)
    assert len(chapters_vn) == 2

    # Under 2 headings -> single chapter
    text_short = "Chapter 1: The Only One\nContent"
    chapters_single = split_chapters(text_short)
    assert len(chapters_single) == 1
    assert chapters_single[0].title == "Full text"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/bin/pytest backend/tests/application/test_narrate_text.py backend/tests/application/test_chapters.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `prepare_narration_text` and `split_chapters`**

In `backend/app/application/pipeline/parse.py`:
- Implement `prepare_narration_text(text: str) -> str`:
  - Normalize line breaks: `text = text.replace("\r\n", "\n").replace("\r", "\n")`
  - Convert full-width spaces: `text = text.replace("\u3000", "  ")`
  - Strip decorative divider lines: `re.sub(r"(?m)^\s*([*=-]\s*){3,}\s*$", "", text)`
  - Strip trailing line whitespace: `\n`.join(line.rstrip() for line in text.split("\n"))
  - Collapse 3+ newlines to 2: `re.sub(r"\n{3,}", "\n\n", text)`

In `backend/app/application/pipeline/chapters.py`:
- Define `Chapter` dataclass (`index: int`, `title: str`, `body: str`, `char_count: int`).
- Implement `split_chapters(text: str) -> list[Chapter]`:
  - Compile regex for headings:
    - Chinese: `r"^第[0-9一二三四五六七八九十百千万]+[章回节卷].*"`
    - Vietnamese: `r"^(?:Chương|Hồi)\s+\d+.*"`
    - English: `r"^(?:Chapter|Book|Act)\s+\d+.*"`
  - Find all heading matches (using `re.MULTILINE`).
  - If matches < 2, return `[Chapter(index=1, title="Full text", body=text.strip(), char_count=len(text.strip()))]`.
  - Otherwise slice text into chapters, recording title and body.
- Implement `chunk_by_chapter(chapters: list[Chapter], max_chars: int) -> list[Chunk]`:
  - For each chapter, chunk its body with `chunk_source` and assign sequential indices across chapters.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/bin/pytest backend/tests/application/test_narrate_text.py backend/tests/application/test_chapters.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/pipeline/parse.py backend/app/application/pipeline/chapters.py backend/tests/application/test_narrate_text.py backend/tests/application/test_chapters.py
git commit -m "feat(pipeline): add narration text preparation and chapter splitting"
```

---

### Task 4: Language Detection Candidates, Thresholds & Settings

**Files:**
- Modify: `backend/app/config/settings.py`
- Modify: `.env.example`
- Modify: `backend/app/domain/ports.py`
- Modify: `backend/app/providers/language_detector/cpu.py`
- Modify: `backend/tests/providers/test_cpu_language_detector.py`
- Modify: `backend/tests/test_env_example.py`

**Interfaces:**
- Consumes: `Settings`, `LanguageDetector` port
- Produces:
  - Settings: `LANGUAGE_CONFIRM_THRESHOLD`, `TTS_PREVIEW_RATE_PER_MINUTE`, `ESTIMATE_CHARS_PER_SECOND`, `ESTIMATE_TRANSLATION_EXPANSION`
  - `LanguageDetector.detect_candidates(text: str) -> list[tuple[str, float]]`

- [ ] **Step 1: Write the failing test**

In `backend/tests/providers/test_cpu_language_detector.py`:
```python
def test_detect_candidates_returns_sorted_probabilities():
    from app.providers.language_detector.cpu import CpuLanguageDetector

    detector = CpuLanguageDetector()
    candidates = detector.detect_candidates("Đây là một đoạn văn bản tiếng Việt mẫu để kiểm tra phát hiện ngôn ngữ.")
    assert len(candidates) >= 1
    lang, conf = candidates[0]
    assert lang == "vi-VN"
    assert conf > 0.7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/providers/test_cpu_language_detector.py -k test_detect_candidates -v`
Expected: FAIL with `AttributeError: 'CpuLanguageDetector' object has no attribute 'detect_candidates'`

- [ ] **Step 3: Implement settings and `detect_candidates`**

In `backend/app/config/settings.py`:
Add:
- `LANGUAGE_CONFIRM_THRESHOLD: float = 0.70`
- `TTS_PREVIEW_RATE_PER_MINUTE: int = 150`
- `ESTIMATE_CHARS_PER_SECOND: float = 15.0`
- `ESTIMATE_TRANSLATION_EXPANSION: float = 1.2`

Update `.env.example` with these defaults and adjust `backend/tests/test_env_example.py`.

In `backend/app/domain/ports.py`:
Add `detect_candidates(self, text: str) -> list[tuple[str, float]]` to `LanguageDetector` abstract class.

In `backend/app/providers/language_detector/cpu.py`:
Implement `detect_candidates`:
- Use `langdetect.detector.DetectorFactory.create()` to get candidate probabilities.
- Normalize language codes with `normalize_language_code()`.
- Sort by confidence descending, capped at top 3, return `list[tuple[str, float]]`.

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/pytest backend/tests/providers/test_cpu_language_detector.py backend/tests/test_env_example.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config/settings.py .env.example backend/app/domain/ports.py backend/app/providers/language_detector/cpu.py backend/tests/providers/test_cpu_language_detector.py backend/tests/test_env_example.py
git commit -m "feat(detector): add detect_candidates and estimation settings"
```

---

### Task 5: Voice Resolution with Language-Family Fallback & Edge Languages

**Files:**
- Modify: `backend/app/domain/ports.py`
- Modify: `backend/app/providers/tts/edge.py`
- Modify: `backend/app/providers/fake.py`
- Modify: `backend/app/application/pipeline/tts.py`
- Modify: `backend/tests/providers/test_edge.py`
- Modify: `backend/tests/application/test_create_job.py`

**Interfaces:**
- Consumes: `TTSProvider`
- Produces:
  - `TTSProvider.languages() -> set[str]`
  - `resolve_voice(tts: TTSProvider, language: str, voice: str | None = None) -> str`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/application/test_create_job.py`:
```python
def test_resolve_voice_family_fallback_and_cantonese_guard():
    from app.application.pipeline.tts import resolve_voice
    from app.domain.audio import Voice
    from app.domain.errors import DomainError, ErrorType
    from app.providers.fake import FakeTTSProvider

    fake_tts = FakeTTSProvider()
    fake_tts.voices_list = [
        Voice(id="en-GB-SoniaNeural", name="Sonia", language="en-GB", gender="female"),
        Voice(id="zh-HK-HiuGaaiNeural", name="HiuGaai", language="zh-HK", gender="female"),
        Voice(id="zh-CN-XiaoxiaoNeural", name="Xiaoxiao", language="zh-CN", gender="female"),
    ]

    # Family fallback: en-US falls back to en-GB
    voice = resolve_voice(fake_tts, "en-US", None)
    assert voice == "en-GB-SoniaNeural"

    # Cantonese guard: zh-HK never falls back to zh-CN
    fake_cantonese_only = FakeTTSProvider()
    fake_cantonese_only.voices_list = [
        Voice(id="zh-HK-HiuGaaiNeural", name="HiuGaai", language="zh-HK", gender="female")
    ]
    with pytest.raises(DomainError) as exc_info:
        resolve_voice(fake_cantonese_only, "zh-CN", None)
    assert exc_info.value.error_type == ErrorType.UNSUPPORTED_LANGUAGE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/application/test_create_job.py -k test_resolve_voice -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_voice'`

- [ ] **Step 3: Implement `languages()` and `resolve_voice`**

In `backend/app/domain/ports.py`:
Add `languages(self) -> set[str]` to `TTSProvider`.

In `backend/app/providers/tts/edge.py` and `backend/app/providers/fake.py`:
Implement `languages(self) -> set[str]`: return set of distinct `v.language` from voices.

In `backend/app/application/pipeline/tts.py`:
Implement `resolve_voice(tts: TTSProvider, language: str, voice: str | None = None) -> str`:
- If `voice` provided: verify it exists in `tts.list_voices()` (or `tts.list_voices(language)`). If not found, raise `DomainError(ErrorType.UNSUPPORTED_LANGUAGE, ...)`.
- If `voice` is None:
  - Try exact match: `voices = await tts.list_voices(language)`. If found, pick first/preferred.
  - Try family fallback: extract primary subtag (e.g. `en` from `en-US`). Find voices matching primary subtag.
  - Guard (spec R2): If `language == "zh-HK"`, DO NOT match `zh-CN` or `zh-TW`. If `language in {"zh-CN", "zh-TW", "zh"}`, DO NOT match `zh-HK`.
  - If no voice found, raise `DomainError(ErrorType.UNSUPPORTED_LANGUAGE, f"No voice found for language {language}")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/pytest backend/tests/application/test_create_job.py -k test_resolve_voice -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/ports.py backend/app/providers/tts/edge.py backend/app/providers/fake.py backend/app/application/pipeline/tts.py backend/tests/application/test_create_job.py backend/tests/providers/test_edge.py
git commit -m "feat(tts): add provider languages() and resolve_voice with family fallback"
```

---

### Task 6: Job Service Validation & Capabilities (`narrate_languages`, `estimate_defaults`)

**Files:**
- Modify: `backend/app/application/jobs.py`
- Modify: `backend/app/application/capabilities.py`
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/parse.py`
- Modify: `backend/app/config/factory.py`
- Modify: `backend/tests/application/test_create_job.py`
- Modify: `backend/tests/api/test_capabilities.py`

**Interfaces:**
- Consumes: `CreateJobCommand`, `JobService`, `CapabilitiesService`
- Produces:
  - `CreateJobCommand.mode: JobMode`
  - `CapabilitiesResponse.narrate_languages: list[str]`
  - `CapabilitiesResponse.estimate_defaults: dict`
  - Narrate job validation in `JobService.create`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/application/test_create_job.py`:
```python
def test_create_narrate_job_validation(job_service):
    from app.domain.jobs import JobMode
    from app.domain.errors import DomainError, ErrorType

    # Narrate rejects translation profile
    with pytest.raises(DomainError) as exc:
        job_service.create(CreateJobCommand(
            text="Hello",
            source_language="en-US",
            target_language="en-US",
            mode=JobMode.NARRATE,
            translation_profile_id="some-profile",
        ))
    assert exc.value.error_type == ErrorType.INVALID_INPUT
```

In `backend/tests/api/test_capabilities.py`:
```python
def test_capabilities_includes_narrate_languages(client):
    res = client.get("/api/capabilities")
    assert res.status_code == 200
    data = res.json()
    assert "narrate_languages" in data
    assert "estimate_defaults" in data
    assert len(data["narrate_languages"]) >= len(data["languages"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/bin/pytest backend/tests/application/test_create_job.py backend/tests/api/test_capabilities.py -k "narrate" -v`
Expected: FAIL

- [ ] **Step 3: Implement validation, capabilities and factory wiring**

In `backend/app/application/jobs.py`:
- Add `mode: JobMode = JobMode.TRANSLATE` to `CreateJobCommand`.
- Inject `tts: TTSProvider` and `detector: LanguageDetector` into `JobService`.
- In `JobService.create()`:
  - If `command.mode == JobMode.NARRATE`:
    - If `command.translation_profile_id` is set -> raise `DomainError(ErrorType.INVALID_INPUT, "translation profiles not allowed in narrate mode")`.
    - If `command.source_language == "auto"`: resolve with `detector.detect(sample)`.
    - Force `target_language = resolved_source`.
    - Validate/resolve voice with `resolve_voice(self.tts, resolved_source, command.voice)`.

In `backend/app/application/capabilities.py`:
- In `CapabilitiesService`, add `narrate_languages = sorted(tts.languages())`.
- Add `estimate_defaults`: `{"chars_per_second": settings.ESTIMATE_CHARS_PER_SECOND, "preview_wpm": settings.TTS_PREVIEW_RATE_PER_MINUTE}`.

In `backend/app/api/schemas.py`:
- Update `CreateJobJsonBody` with `mode: JobMode = JobMode.TRANSLATE`.
- Update `JobStatusResponse` with `mode: JobMode`.
- Update `CapabilitiesResponse` with `narrate_languages: list[str]` and `estimate_defaults: dict[str, float]`.

In `backend/app/api/parse.py`:
- Extract `mode` in `_parse_multipart` and validate against `JobMode`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/bin/pytest backend/tests/application/test_create_job.py backend/tests/api/test_capabilities.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/jobs.py backend/app/application/capabilities.py backend/app/api/schemas.py backend/app/api/parse.py backend/app/config/factory.py backend/tests/application/test_create_job.py backend/tests/api/test_capabilities.py
git commit -m "feat(api): wire narrate mode in job creation and capabilities"
```

---

### Task 7: Pipeline Orchestrator Narrate Mode & Same-Language Translate Bypass

**Files:**
- Modify: `backend/app/application/pipeline/orchestrator.py`
- Modify: `backend/app/workers/runner.py`
- Modify: `backend/app/config/factory.py`
- Modify: `backend/tests/application/test_pipeline.py`
- Modify: `backend/tests/workers/test_process_job.py`

**Interfaces:**
- Consumes: `PipelineOrchestrator.run`, `Job`, `_pass_through_chunks`
- Produces:
  - `_run_narrate(job, text)` executing pipeline without `_translate_chunks`
  - Same-language translate bypass for plain text (R6)
  - Null translation provider in worker for narrate jobs

- [ ] **Step 1: Write the failing tests**

In `backend/tests/application/test_pipeline.py`:
```python
@pytest.mark.asyncio
async def test_narrate_mode_executes_zero_translation_calls(orchestrator_fixture):
    orchestrator, translation_mock, tts_mock, job_store = orchestrator_fixture
    job = Job(
        job_id="test-narrate-1",
        source_language="vi-VN",
        target_language="vi-VN",
        mode=JobMode.NARRATE,
        voice="vi-VN-HoaiMyNeural",
    )
    await job_store.save(job)
    await orchestrator.run(job, "Đây là văn bản thử nghiệm không cần dịch.")

    assert len(translation_mock.calls) == 0
    saved = await job_store.get(job.job_id)
    assert saved.status == JobStatus.COMPLETED

@pytest.mark.asyncio
async def test_translate_same_language_bypasses_translation(orchestrator_fixture):
    orchestrator, translation_mock, tts_mock, job_store = orchestrator_fixture
    job = Job(
        job_id="test-trans-same-1",
        source_language="en-US",
        target_language="en-US",
        mode=JobMode.TRANSLATE,
        voice="en-US-JennyNeural",
    )
    await job_store.save(job)
    await orchestrator.run(job, "English text with same source and target.")

    assert len(translation_mock.calls) == 0
    saved = await job_store.get(job.job_id)
    assert saved.status == JobStatus.COMPLETED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/application/test_pipeline.py -k "test_narrate_mode or test_translate_same" -v`
Expected: FAIL

- [ ] **Step 3: Implement `_run_narrate` and bypass logic in `orchestrator.py`**

In `backend/app/application/pipeline/orchestrator.py`:
- In `run(job, text)`:
  - If `job.mode == JobMode.UNKNOWN`: fail immediately with `DomainError(ErrorType.INVALID_INPUT, "Unknown job mode")`.
  - If `job.mode == JobMode.NARRATE`:
    - If `is_srt_document(text)`: raise `DomainError(ErrorType.INVALID_INPUT, "SRT narrate mode is coming in phase 2")`.
    - Return `await self._run_narrate(job, text)`
  - In `_run_narrate(job, text)`:
    - `cleaned_text = prepare_narration_text(text)`
    - `chunks = chunk_source(cleaned_text)`
    - `translated_chunks = await self._pass_through_chunks(job, chunks)` (records `STAGE_TRANSLATED` checkpoints)
    - `narrated_chunks = await self._narrate_chunks(job, translated_chunks)`
    - `audio_artifacts = await self._generate_audio(job, narrated_chunks, settings)`
    - `await self._finish_audio(job, audio_artifacts, format)`
  - In `_run_plain(job, text)`:
    - If `job.source_language == job.target_language`:
      - Skip `_translate_chunks`, use `await self._pass_through_chunks(job, chunks)`.

In `backend/app/config/factory.py` & `backend/app/workers/runner.py`:
- When constructing orchestrator for a job with `mode == JobMode.NARRATE`, if translation provider build fails or is missing, use `NullTranslationProvider()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/bin/pytest backend/tests/application/test_pipeline.py backend/tests/workers/test_process_job.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/application/pipeline/orchestrator.py backend/app/workers/runner.py backend/app/config/factory.py backend/tests/application/test_pipeline.py backend/tests/workers/test_process_job.py
git commit -m "feat(pipeline): implement _run_narrate and same-language translation bypass"
```

---

### Task 8: Pre-submit Endpoints (`/api/detect-language`, `/api/analyze`, `/api/tts/preview`)

**Files:**
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/routes.py`
- Modify: `backend/app/api/parse.py`
- Modify: `backend/tests/api/test_jobs.py`

**Interfaces:**
- Consumes: `LanguageDetector`, `TTSProvider`, `split_chapters`, `_decode_source_text`
- Produces:
  - `POST /api/detect-language`
  - `POST /api/analyze`
  - `POST /api/tts/preview`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/api/test_jobs.py`:
```python
def test_detect_language_endpoint(client):
    res = client.post("/api/detect-language", json={"text": "Xin chào thế giới, đây là tiếng Việt."})
    assert res.status_code == 200
    data = res.json()
    assert data["language"] == "vi-VN"
    assert "candidates" in data
    assert "needs_confirmation" in data

def test_analyze_endpoint(client):
    sample = "第一章 开篇\n这是中文测试。\n第二章 发展\n继续测试。"
    res = client.post("/api/analyze", json={"text": sample})
    assert res.status_code == 200
    data = res.json()
    assert data["encoding"] == "utf-8"
    assert data["chapters_count"] == 2
    assert "duration_estimate_seconds" in data
    assert "process_time_estimate_seconds" in data

def test_tts_preview_endpoint(client):
    res = client.post("/api/tts/preview", json={"text": "Hello world", "voice": "en-US-JennyNeural"})
    assert res.status_code == 200
    data = res.json()
    assert "audio_base64" in data
    assert data["mime_type"] == "audio/mpeg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/api/test_jobs.py -k "test_detect_language_endpoint or test_analyze_endpoint or test_tts_preview" -v`
Expected: FAIL with 404 Not Found

- [ ] **Step 3: Implement schemas and routes**

In `backend/app/api/schemas.py`:
Define `LanguageCandidate`, `DetectLanguageRequest`, `DetectLanguageResponse`, `ChapterSummary`, `AnalyzeResponse`, `TTSPreviewRequest`, `TTSPreviewResponse`.

In `backend/app/api/routes.py`:
- `POST /api/detect-language`: Slice text to <= 8 KB, run `detector.detect_candidates()`, evaluate `confidence < settings.LANGUAGE_CONFIRM_THRESHOLD`, return candidate list and preview lines.
- `POST /api/analyze`: Accept JSON or multipart file. Decode using `_decode_source_text` (retaining encoding). Detect language with detector. Split chapters with `split_chapters`. Compute estimates based on char count and settings. Return `AnalyzeResponse`.
- `POST /api/tts/preview`: Validate `len(request.text) <= 300`. Synthesize short snippet using `TTSProvider.synthesize(text, voice=request.voice, speed=request.speed)`. Return base64 encoded audio.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/bin/pytest backend/tests/api/test_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/routes.py backend/app/api/parse.py backend/tests/api/test_jobs.py
git commit -m "feat(api): add detect-language, analyze, and tts-preview endpoints"
```

---

### Task 9: Frontend Mode Toggle, Analysis Panel & Friendly Errors

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/lib/jobUi.ts`
- Modify: `frontend/src/components/JobForm.tsx`
- Modify: `frontend/src/components/JobProgress.tsx`
- Modify: `frontend/src/lib/jobUi.test.ts`
- Modify: `frontend/src/components/JobForm.test.tsx`
- Modify: `frontend/src/components/JobProgress.test.tsx`

**Interfaces:**
- Consumes: `/api/capabilities`, `/api/analyze`, `/api/detect-language`, `/api/tts/preview`
- Produces:
    - Mode toggle tab ("Translate" vs "Narrate (No model cost)")
    - Pre-submit analysis drawer/panel
    - Same-language Translate banner
    - Friendly error message translation

- [ ] **Step 1: Write the failing tests**

In `frontend/src/lib/jobUi.test.ts`:
```ts
import { friendlyError } from './jobUi';

describe('friendlyError', () => {
  it('maps PROVIDER_CONFIGURATION_ERROR to friendly text', () => {
    const err = friendlyError('PROVIDER_CONFIGURATION_ERROR', 'missing key');
    expect(err.summary).toContain('Service configuration issue');
    expect(err.details).toBe('missing key');
  });
});
```

In `frontend/src/components/JobForm.test.tsx`:
```tsx
it('renders Narrate mode toggle with No model cost label', () => {
  render(<JobForm capabilities={mockCapabilities} onSubmit={vi.fn()} />);
  expect(screen.getByText(/Narrate/i)).toBeInTheDocument();
  expect(screen.getByText(/No model cost/i)).toBeInTheDocument();
});

it('shows switch banner when source equals target in translate mode', async () => {
  render(<JobForm capabilities={mockCapabilities} onSubmit={vi.fn()} />);
  // change source and target to same language
  // assert "switch to Narrate directly?" banner is visible
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL

- [ ] **Step 3: Implement frontend types, client, UI components**

In `frontend/src/api/types.ts`:
Add `JobMode`, update `CreateJobPayload`, `JobStatusResponse`, `AnalyzeResponse`, `CapabilitiesResponse`.

In `frontend/src/api/client.ts`:
Add `detectLanguage()`, `analyze()`, `previewTts()`.

In `frontend/src/lib/jobUi.ts`:
Implement `friendlyError(errorType, message)` mapping domain errors (`PROVIDER_CONFIGURATION_ERROR`, `UNSUPPORTED_LANGUAGE`, `TIMEOUT`, `PROVIDER_RATE_LIMIT`, etc.) to clear human-readable sentences.

In `frontend/src/components/JobForm.tsx`:
- Add mode switch buttons: "Translate" and "Narrate (No model cost)".
- When switching modes, retain selected language if present in new list (`languages` vs `narrate_languages`).
- If in Translate mode and `sourceLanguage === targetLanguage`, show suggestion banner: "Same language — switch to Narrate directly?" with quick action button.
- Add Analysis section: debounce text/file changes, invoke `/api/analyze`, display detected encoding, confidence warning if < 70%, chapter count, and duration estimates.
- Add "Listen" button with preview audio player.

In `frontend/src/components/JobProgress.tsx`:
Use `friendlyError` to show human-friendly explanation, collapsing technical error code under "Technical details".

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat(frontend): add narrate mode toggle, analysis panel, and friendly errors"
```

---

### Task 10: Documentation & ADR Updates

**Files:**
- Modify: `README.md`
- Modify: `docs/adr/0005-translation-narration-tts-separation.md`

**Interfaces:**
- Updates public API and architectural documentation.

- [ ] **Step 1: Write test or check doc integrity**

Run `python3 .github/scripts/scan_repo.py` to check doc consistency.

- [ ] **Step 2: Implement documentation updates**

In `README.md`:
- Document `mode: "narrate"` in the job creation section.
- Document new endpoints: `/api/detect-language`, `/api/analyze`, `/api/tts/preview`.
- Document `narrate_languages` in capabilities.

In `docs/adr/0005-translation-narration-tts-separation.md`:
- Add ADR addendum documenting Narrate mode, pass-through chunk reuse, and voice family fallback policy.

- [ ] **Step 3: Run repo verification**

Run: `make check`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add README.md docs/adr/0005-translation-narration-tts-separation.md
git commit -m "docs: document narrate mode, new endpoints, and architecture update"
```
