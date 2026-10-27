# Audit Fixes (8 Items) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 8 findings identified during the codebase audit across Docker Compose configuration, artifact cache file concurrency, DualWrite job store, FFmpeg error reporting, RQ worker failure hook, Gemini fence parsing, Edge TTS voice caching, and frontend polling resilience.

**Architecture:**
- Configuration: forward Groq and OpenRouter environment variables in `docker-compose.yml`.
- Storage: add unique UUID suffixes to cache temporary files to avoid race conditions during concurrent writes.
- Job Store: refine and document `DualWriteJobStore` disk-authoritative invariants.
- Audio: capture and surface `stderr` upon FFmpeg non-zero exit code.
- Workers: preserve `DomainError` error type and message in `_map_error`.
- Providers: harmonize Gemini markdown code fence regex with `openai_compatible.py`.
- TTS: index voices by language in `EdgeTTSProvider` to eliminate O(N) linear scans on capabilities queries.
- Frontend: add transient error retry with backoff to `useJob` hook to prevent polling termination on single network blips.

**Tech Stack:** Python 3.12, FastAPI, RQ, Redis, FFmpeg, React 18, TypeScript, Vitest, Pytest.

---

### Task 1: Add Groq and OpenRouter Environment Variables to Docker Compose

**Files:**
- Modify: `docker-compose.yml:L35-45, L81-92`

- [ ] **Step 1: Edit `docker-compose.yml` to include Groq and OpenRouter environment variables in `api` service**
  Add under `api.environment`:
  ```yaml
      GROQ_API_KEY: ${GROQ_API_KEY:-}
      GROQ_TRANSLATION_MODEL: ${GROQ_TRANSLATION_MODEL:-qwen/qwen3.8-27b}
      GROQ_BASE_URL: ${GROQ_BASE_URL:-https://api.groq.com/openai/v1}
      GROQ_HTTP_TIMEOUT_SECONDS: ${GROQ_HTTP_TIMEOUT_SECONDS:-60}
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}
      OPENROUTER_TRANSLATION_MODEL: ${OPENROUTER_TRANSLATION_MODEL:-deepseek/deepseek-v4-flash}
      OPENROUTER_BASE_URL: ${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}
      OPENROUTER_HTTP_TIMEOUT_SECONDS: ${OPENROUTER_HTTP_TIMEOUT_SECONDS:-60}
  ```

- [ ] **Step 2: Edit `docker-compose.yml` to include Groq and OpenRouter environment variables in `worker` service**
  Add the identical variables under `worker.environment`.

- [ ] **Step 3: Verify `docker-compose.yml` syntax**
  Run: `docker compose config > /dev/null` or parse with python YAML parser.

---

### Task 2: Prevent Cache File Collision in `FilesystemArtifactCache`

**Files:**
- Modify: `backend/app/infrastructure/artifact_cache_fs.py:L30-38`
- Test: `backend/tests/infrastructure/test_artifact_cache_fs.py`

- [ ] **Step 1: Write a test verifying unique temp file naming on concurrent `put`**
  In `test_artifact_cache_fs.py`, verify `put` creates a temp file with UUID and does not collide.

- [ ] **Step 2: Update `FilesystemArtifactCache.put` in `artifact_cache_fs.py`**
  ```python
  import uuid
  ...
  tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
  ```

- [ ] **Step 3: Run pytest on artifact cache**
  Run: `./backend/.venv/bin/pytest backend/tests/infrastructure/test_artifact_cache_fs.py -v`
  Expected: PASS

---

### Task 3: Clarify & Optimize `DualWriteJobStore` Read-Repair

**Files:**
- Modify: `backend/app/infrastructure/job_store.py:L39-53`
- Test: `backend/tests/infrastructure/test_job_store.py`

- [ ] **Step 1: Verify existing test suite for `DualWriteJobStore`**
  Run: `./backend/.venv/bin/pytest backend/tests/infrastructure/test_job_store.py -v`
  Verify that the tests assert filesystem-authoritative behavior.

- [ ] **Step 2: Update docstring and inline comments in `job_store.py`**
  Clarify that disk read is authoritative per ADR/M12 specification to prevent stale Redis data from hiding terminal states, and ensure any Redis read errors during cache compare fail closed gracefully.

- [ ] **Step 3: Re-run tests**
  Run: `./backend/.venv/bin/pytest backend/tests/infrastructure/test_job_store.py -v`
  Expected: PASS

---

### Task 4: Capture and Log FFmpeg `stderr` on Non-Zero Exit Code

**Files:**
- Modify: `backend/app/infrastructure/ffmpeg_audio.py:L156-168`
- Test: `backend/tests/infrastructure/test_ffmpeg_audio.py`

- [ ] **Step 1: Update test in `test_ffmpeg_audio.py` to assert detailed error message**
  Update `_FakeProcess` to return simulated stderr bytes and test that `run_ffmpeg_argv` includes or logs stderr.

- [ ] **Step 2: Update `run_ffmpeg_argv` in `ffmpeg_audio.py`**
  ```python
  stdout, stderr = await process.communicate()
  if process.returncode != 0:
      err_msg = stderr.decode("utf-8", errors="replace").strip()
      logger.error("ffmpeg_execution_failed", extra={"argv": list(argv), "stderr": err_msg})
      raise DomainError(
          ErrorType.AUDIO_PROCESSING_FAILED,
          f"ffmpeg failed: {err_msg}" if err_msg else "ffmpeg failed",
      )
  ```

- [ ] **Step 3: Run audio processor tests**
  Run: `./backend/.venv/bin/pytest backend/tests/infrastructure/test_ffmpeg_audio.py -v`
  Expected: PASS

---

### Task 5: Preserve `DomainError` in Worker RQ Failure Hook

**Files:**
- Modify: `backend/app/workers/rq_failure.py:L60-74`
- Test: `backend/tests/workers/test_rq_failure.py`

- [ ] **Step 1: Add unit test in `test_rq_failure.py` for `DomainError` propagation**
  Test that `DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "custom msg")` maps to `(ErrorType.UNSUPPORTED_LANGUAGE, "custom msg")`.

- [ ] **Step 2: Implement check in `rq_failure.py:_map_error`**
  ```python
  from app.domain.errors import DomainError, ErrorType
  ...
  if isinstance(exc_value, DomainError):
      return exc_value.error_type, exc_value.message
  ```

- [ ] **Step 3: Run worker tests**
  Run: `./backend/.venv/bin/pytest backend/tests/workers/test_rq_failure.py -v`
  Expected: PASS

---

### Task 6: Support Windows CRLF & Trailing Whitespace in Gemini Fence Regex

**Files:**
- Modify: `backend/app/providers/translation/gemini.py:L81-86`
- Test: `backend/tests/providers/test_gemini_translation.py`

- [ ] **Step 1: Add unit test in `test_gemini_translation.py` for CRLF code fences**
  Test string ````markdown \r\nTranslated\r\n```` is stripped to `Translated`.

- [ ] **Step 2: Update `_strip_markdown_fences` in `gemini.py`**
  ```python
  match = re.match(r"^```[a-zA-Z0-9_-]*[ \t]*\r?\n(.*)\r?\n```[ \t]*$", cleaned, re.DOTALL)
  ```

- [ ] **Step 3: Run Gemini provider tests**
  Run: `./backend/.venv/bin/pytest backend/tests/providers/test_gemini_translation.py -v`
  Expected: PASS

---

### Task 7: Cache Edge TTS Voices Grouped by Language

**Files:**
- Modify: `backend/app/providers/tts/edge.py:L41-53`
- Test: `backend/tests/providers/test_edge.py`

- [ ] **Step 1: Add unit test verifying `voices_for` lookups and voice indexing**
  Test in `test_edge.py` that multiple calls to `voices_for` across different languages return correctly ordered results without re-scanning.

- [ ] **Step 2: Update `EdgeTTSProvider` in `edge.py`**
  Add `self._voices_by_language: dict[str, list[Voice]] | None = None` and build the dictionary once when `_all_voices()` is loaded:
  ```python
  def voices_for(self, language: str) -> Sequence[Voice]:
      if self._voices_by_language is None:
          mapping: dict[str, list[Voice]] = {}
          for voice in self._all_voices():
              mapping.setdefault(voice.language, []).append(voice)
          self._voices_by_language = mapping
      matched = self._voices_by_language.get(language, [])
      ...
  ```

- [ ] **Step 3: Run Edge TTS tests**
  Run: `./backend/.venv/bin/pytest backend/tests/providers/test_edge.py -v`
  Expected: PASS

---

### Task 8: Resilient Polling with Transient Retry in Frontend `useJob`

**Files:**
- Modify: `frontend/src/hooks/useJob.ts:L28-66`
- Test: `frontend/src/App.test.tsx` (and `npm test -- --run`)

- [ ] **Step 1: Update `useJob.ts` with retry count before setting error**
  Add `failureCountRef = useRef(0)`.
  When `pollOnce(jobId)` fails:
  If failureCount < 3: increment failureCount, schedule next poll.
  If failureCount >= 3: set `status: "error"`.
  When `pollOnce(jobId)` succeeds: reset failureCount to 0.

- [ ] **Step 2: Run frontend test suite**
  Run: `cd frontend && npm test -- --run`
  Expected: All 47 tests PASS.

---

### Task 9: Full Regression Verification
- Run backend pytest: `./backend/.venv/bin/pytest backend/tests/ -v`
- Run ruff linter & formatter: `./backend/.venv/bin/ruff check backend/ && ./backend/.venv/bin/ruff format backend/`
- Run frontend tests: `cd frontend && npm test -- --run`
