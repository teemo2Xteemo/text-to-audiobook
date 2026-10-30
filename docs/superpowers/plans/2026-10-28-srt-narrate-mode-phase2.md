# SRT Narrate Mode (Phase 2) Implementation Plan

**Goal:** Enable SRT/WebVTT subtitle inputs in Narrate mode (`mode="narrate"`), converting subtitles into clean continuous narrative text for audio synthesis without translation or 400 errors.

**Architecture:**
- Remove the Phase 1 guard (`DomainError(ErrorType.INVALID_INPUT, "SRT narrate mode is coming in phase 2")`) in `backend/app/application/jobs.py` and `backend/app/application/pipeline/orchestrator.py`.
- Enhance `strip_srt` in `backend/app/application/pipeline/parse.py` to strip ASS tags (e.g. `{\an8}`, `{\i1}`) and drop sound-effect-only cues (e.g. `[Music]`, `[Applause]`, `♪...♪`).
- Update frontend `stripSrtToText` in `frontend/src/lib/jobUi.ts` to match ASS and sound-effect cleaning.
- Update frontend `JobForm.tsx` to indicate that SRT will be automatically converted to continuous narration when Narrate mode is active.
- Update existing Phase 1 rejection tests in `backend/tests/api/test_jobs.py` and `backend/tests/application/test_pipeline.py` to verify successful job creation and end-to-end execution of SRT in Narrate mode.

---

### Task 1: Enhance `strip_srt` and `stripSrtToText` (ASS Tags & Sound Effect Filtering)

**Files:**
- Modify: `backend/app/application/pipeline/parse.py`
- Modify: `backend/tests/application/test_pipeline.py`
- Modify: `frontend/src/lib/jobUi.ts`
- Modify: `frontend/src/lib/jobUi.test.ts`

**Steps:**
1. Write failing tests for `strip_srt` covering ASS tags (`{\an8}`) and sound effect cues (`[Music]`, `♪...♪`) in `backend/tests/application/test_pipeline.py`.
2. Implement ASS tag stripping (`_ASS_TAG_RE = re.compile(r"\{[^}]*\}")`) and sound effect line filtering in `backend/app/application/pipeline/parse.py`.
3. Verify backend tests pass.
4. Write failing tests for `stripSrtToText` in `frontend/src/lib/jobUi.test.ts`.
5. Implement matching ASS tag and sound effect stripping in `frontend/src/lib/jobUi.ts`.
6. Run frontend vitest to verify tests pass.

---

### Task 2: Allow SRT in Narrate Mode (Backend API & Pipeline Orchestrator)

**Files:**
- Modify: `backend/app/application/jobs.py`
- Modify: `backend/app/application/pipeline/orchestrator.py`
- Modify: `backend/tests/api/test_jobs.py`
- Modify: `backend/tests/application/test_pipeline.py`

**Steps:**
1. Update API test in `backend/tests/api/test_jobs.py` (`test_narrate_mode_with_srt_subtitles_succeeds`) to assert 201/202 status and job creation rather than 400 error.
2. Update pipeline test in `backend/tests/application/test_pipeline.py` (`test_srt_with_narrate_mode_produces_audio_successfully`) to assert `JobStatus.COMPLETED` rather than `JobStatus.FAILED`.
3. Remove the `is_srt_document(text)` check in `backend/app/application/jobs.py` (L100-104) and `backend/app/application/pipeline/orchestrator.py` (L108-112).
4. Run `backend/.venv/bin/pytest backend/tests/` to verify tests pass.

---

### Task 3: Update Frontend UX for SRT in Narrate Mode

**Files:**
- Modify: `frontend/src/components/JobForm.tsx`
- Modify: `frontend/src/components/JobForm.test.tsx`

**Steps:**
1. In `JobForm.tsx`, when `mode === "narrate"` and `metrics.isSrt` is true, display a badge/note clarifying that SRT timecodes will be converted to continuous narration.
2. Add a test in `frontend/src/components/JobForm.test.tsx` verifying the note is shown.
3. Run `npm --prefix frontend test -- --run` to verify all tests pass.

---

### Task 4: Full Verification & Quality Gates

**Steps:**
1. Run all backend tests: `backend/.venv/bin/pytest backend/tests/ -q`
2. Run backend linter/formatter: `backend/.venv/bin/ruff check backend/`
3. Run repository policy hooks: `python3 .cursor/hooks/selftest.py` and `python3 .github/scripts/scan_repo.py`
4. Run frontend tests and build: `npm --prefix frontend test -- --run && npm --prefix frontend run build`
