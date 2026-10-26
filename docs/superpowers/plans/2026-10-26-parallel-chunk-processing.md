# Parallel Chunk Processing Implementation Plan

## Proposed Changes

### 1. Configuration (`backend/app/config/settings.py`)
- Add fields:
  - `chunk_translation_concurrency: int = Field(default=2, ge=1)`
  - `chunk_tts_concurrency: int = Field(default=2, ge=1)`
- Add documentation/comments in settings.

### 2. Factory (`backend/app/config/factory.py`)
- In `build_orchestrator`:
  Pass `translation_concurrency=settings.chunk_translation_concurrency` and `tts_concurrency=settings.chunk_tts_concurrency`.

### 3. Application Pipeline Orchestrator (`backend/app/application/pipeline/orchestrator.py`)
- Accept `translation_concurrency: int = 1` and `tts_concurrency: int = 1` in `PipelineOrchestrator.__init__`.
- Update `_translate_chunks`:
  - Run chunk translation concurrently using `asyncio.Semaphore(self._translation_concurrency)` and `asyncio.Lock` for checkpoint/progress persistence.
  - If any chunk fails, cleanly cancel in-flight tasks and propagate the exception.
- Update `_generate_audio`:
  - Run chunk TTS + normalization concurrently using `asyncio.Semaphore(self._tts_concurrency)` and `asyncio.Lock` for checkpoint/progress persistence.
  - If any chunk fails, cleanly cancel in-flight tasks and propagate the exception.
- Update `_narrate_chunks`:
  - Process chunks with atomic progress tracking.
- Progress tracking:
  - Maintain monotonically increasing `chunk_current` based on completed chunk count.

### 4. Environment & Deployment (`.env`, `docker-compose.yml`)
- Add `CHUNK_TRANSLATION_CONCURRENCY=3` and `CHUNK_TTS_CONCURRENCY=2` to `.env` and `docker-compose.yml` environment blocks.

### 5. Tests
- Unit test in `backend/tests/test_settings.py` for new concurrency settings and validation.
- Unit test in `backend/tests/config/test_factory.py` asserting concurrency injection into orchestrator.
- New unit test file `backend/tests/application/test_pipeline_concurrency.py`:
  - Tests concurrent translation with controlled concurrency (e.g. concurrency=3).
  - Verifies that multiple chunks are processed in parallel (checking peak active concurrent operations).
  - Verifies that `chunk_current` increases monotonically to `chunk_total`.
  - Verifies that if one chunk fails, other tasks are cancelled cleanly and status becomes `FAILED`.
  - Verifies checkpoint and resume when partially completed in concurrent mode.
- Run full regression test suite (`pytest -m "not integration"`).
- Re-run linter (`ruff check` and `ruff format`).

---

## Verification Plan

### Automated Tests
1. `backend/.venv/bin/pytest backend/tests/test_settings.py`
2. `backend/.venv/bin/pytest backend/tests/config/test_factory.py`
3. `backend/.venv/bin/pytest backend/tests/application/test_pipeline.py`
4. `backend/.venv/bin/pytest backend/tests/application/test_pipeline_concurrency.py`
5. `backend/.venv/bin/ruff check backend`
6. `python3 .cursor/hooks/selftest.py`
7. `python3 .github/scripts/scan_repo.py`

### Docker E2E Verification
- Restart worker with new environment variables.
- Run an E2E story job with 5+ chunks on `http://127.0.0.1:8080` to verify parallel processing and correct audio generation.
