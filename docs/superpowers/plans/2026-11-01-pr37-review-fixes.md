# PR #37 Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the verified findings from the PR #37 review (T1, T2, T3, T4, T5), settle T6, and answer T7 with evidence, on branch `worktree-issue-28-phase3`.

**Architecture:** VieNeu voice discovery moves off the event loop. A new `async refresh_voices()` does the network fetch in a thread, and `all_voices()` only returns the cache. Retry budgets get one counter per error class. Provider 4xx codes that cannot succeed on retry stop being retried. Docs cover the RQ timeout for CPU engines.

**Tech Stack:** Python 3.12, FastAPI, urllib + `asyncio.to_thread`, pytest; README/.env.example.

**Spec:** the review comment on PR #37 (https://github.com/teemo2Xteemo/text-to-audiobook/pull/37, "Automated review … head `43da3e9`") and the Cursor Bugbot thread on `backend/app/providers/tts/vieneu.py:94`. Phase 3 plan: `docs/superpowers/plans/2026-10-31-narrate-phase3-tts-engines.md`.

**Out of scope (and why):**
- T7 (voice not reset when the engine changes) is a false positive. The `useEffect` at `frontend/src/components/JobForm.tsx:283-290` resets the voice whenever `targetVoices` changes, `targetVoices` depends on `ttsEngine`, and `JobForm.test.tsx:682` asserts the reset. Task 5 answers this on the PR with that evidence; no code changes.
- These review notes go into one follow-up issue (Task 5) instead of this PR: provider instances shared across services, a cross-job VieNeu semaphore, the `.mp3` extension on WAV chunk files, preview speed for VieNeu, the unbounded preview cache, and compose `user:`/`read_only` hardening.
- T5's suggestion to log the provider error body is not taken. Fish and VieNeu bodies can echo story text, which must not land in logs.

## Global Constraints

- Language-agnostic; domain code calls ports only; never interpolate user input into shell; no secrets in commits or logs.
- Edge stays the default, and a job without `tts_engine` behaves exactly as today. Edge and fake cache keys must not change.
- Never switch engine without a user action.
- `GET /api/capabilities` never returns 500 because of an engine probe. A failed probe gives `available=false`.
- Rate-limit policy values stay the same: `RETRY_RATE_LIMIT_MAX_ATTEMPTS=6`, `RETRY_RATE_LIMIT_BACKOFF_SECONDS=5`, each wait ≤ 120 s. The base policy is unchanged.
- Every new setting goes in `.env.example` (`backend/tests/test_env_example.py`).
- Commit with explicit `git add <paths>`. Never commit `.superpowers/`, `backend/.venv` or `frontend/node_modules`.

## Review Focus

1. **Hung VieNeu sidecar while the UI polls jobs.** `GET /api/jobs/{id}` must still answer quickly while `GET /api/capabilities` waits on VieNeu. Test in Task 1.
2. **VieNeu down for minutes, then back up.** Capabilities show `available=false`, then recover within the 30 s backoff window without a restart. A worker chunk retry re-fetches immediately (backoff 0). Test in Task 1.
3. **Mixed error sequences in one chunk**, e.g. 429 ×4 → `TTS_FAILED` → success. The chunk succeeds, and the base wait is not inflated by earlier 429s. Test in Task 2.
4. **Fish rejects a request (400, 404 or 422)**, e.g. a wrong `reference_id` or text too long. The job fails at once with a clear, non-retryable error instead of retrying 3×. Test in Task 3.
5. **Operator changes `VIENEU_MODEL`.** The cache key changes, so old audio is not replayed. Today's default model gives the same key as before. Test in Task 1.

---

### Task 1: VieNeu voice discovery off the event loop (T1, T4, T2 docs)

**Files:**
- Modify: `backend/app/providers/tts/vieneu.py` (`all_voices`, new `refresh_voices`, `check_available`, constants)
- Modify: `backend/app/application/capabilities.py` (call refresh before `_intersection_languages` when the default provider has it)
- Modify: `backend/app/application/jobs.py` (await refresh before `resolve_voice`)
- Modify: `backend/app/application/pipeline/orchestrator.py:764-788` (`_pick`: await refresh with backoff 0 inside the retried step)
- Modify: `backend/app/config/factory.py:632` (`tts_model_identity`)
- Modify: `README.md` (VieNeu section) and `.env.example` (comment next to `RQ_JOB_TIMEOUT_SECONDS` / VieNeu block)
- Test: `backend/tests/providers/test_vieneu.py`, `backend/tests/api/test_capabilities.py`, `backend/tests/application/test_pipeline.py`, `backend/tests/config/test_factory.py`

**Interfaces:**
- Produces: `VieneuTTSProvider.refresh_voices(self, *, backoff_seconds: float = CAPABILITIES_BACKOFF_SECONDS) -> Awaitable[None]` (async). It runs the `/voices` GET through `asyncio.to_thread`. It does nothing when voices are already cached or when a failure is younger than `backoff_seconds`, and it records `_failed_at = time.monotonic()` after the failed probe returns. It never raises.
- Produces: constant `CAPABILITIES_BACKOFF_SECONDS = 30.0`. It replaces `FAILURE_BACKOFF_SECONDS`, so delete the old name.
- `all_voices()` never does I/O: it returns the cached list or `[]`.
- `check_available()` awaits `refresh_voices()` after `/health` succeeds.
- Callers use `refresh = getattr(tts, "refresh_voices", None)` and `await refresh(...)` when it exists. Edge, Fish and fake have no such method and are unaffected.
- `tts_model_identity` for vieneu returns `f"{settings.vieneu_model}:{settings.vieneu_precision}"`.

- [ ] **Step 1: Write failing tests**

```python
def test_all_voices_does_no_io(): ...
    # opener counts calls; provider.all_voices() == [] and opener.calls == [] before any refresh

async def test_refresh_voices_runs_off_loop_thread(): ...
    # opener records threading.get_ident(); assert it != the test's thread id; all_voices() then lists "vieneu:<name>"

async def test_failure_backoff_counts_from_after_probe(monkeypatch): ...
    # fake clock in vieneu: opener advances clock by 5.0 then raises.
    # refresh() → 1 call; clock +1.0; refresh() → still 1 call (within 30 s); clock +30; refresh() → 2 calls

async def test_refresh_backoff_zero_always_refetches(): ...
    # after a failure, refresh(backoff_seconds=0) → a new call

async def test_capabilities_with_hung_vieneu_does_not_block_loop(): ...  # test_capabilities.py
    # opener sleeps 0.5 s (in thread). Run CapabilitiesService.get() concurrently with a ticker
    # coroutine doing 5 × asyncio.sleep(0.02); assert ticker finished before get() returned,
    # and response has vieneu available=False when the probe ends in an error.

def test_vieneu_identity_follows_model(): ...  # test_factory.py
    assert tts_model_identity(Settings(tts_provider="vieneu", vieneu_model="m2", vieneu_precision="int8")) == "m2:int8"
    assert tts_model_identity(Settings(tts_provider="vieneu")) == "vieneu-v3-turbo:fp32"  # unchanged default key
```

Orchestrator: change the existing `test_voice_service_blip_is_retried_not_unsupported_language` so it uses a provider exposing `refresh_voices`, and assert that refresh is awaited with `backoff_seconds=0` before each selection attempt.

- [ ] **Step 2: Run, FAIL.** `backend/.venv/bin/pytest backend/tests/providers/test_vieneu.py backend/tests/api/test_capabilities.py backend/tests/application/test_pipeline.py backend/tests/config/test_factory.py -q`
- [ ] **Step 3: Implement** per Interfaces. Remove the `shortcut:` comment in `all_voices` (the shortcut is gone). In `CapabilitiesService.get`, refresh the default provider before computing `languages`. Per-engine probes already go through `check_available`.
- [ ] **Step 4: Docs (T2).** Add one paragraph to the README VieNeu section and a comment line in `.env.example`. A CPU sidecar runs at RTF ≈ 0.5–0.7, so `RQ_JOB_TIMEOUT_SECONDS` (default 1800) covers about 45–60 min of audio. Raise it for book-length jobs. A job killed by the timeout ends `FAILED/TIMEOUT`, and `POST /api/jobs/{id}/retry` resumes from checkpoints without regenerating finished chunks.
- [ ] **Step 5: Run** the Step 2 command, then `backend/.venv/bin/pytest backend/tests -q`: PASS.
- [ ] **Step 6: Commit** `fix: VieNeu voice discovery off the event loop, model-aware cache identity (#28)`

### Task 2: Separate retry budgets per error class (T3)

**Files:**
- Modify: `backend/app/application/pipeline/retry.py` (`with_chunk_retry`)
- Test: `backend/tests/application/test_retry_rate_limit.py`

**Interfaces:**
- `with_chunk_retry` signature unchanged. It keeps two counters, `rate_limit_attempt` and `base_attempt`. For each caught error, the counter of that error's class is used for `can_retry = counter + 1 < max_attempts`, and the wait is `min(max(backoff * 2**counter, hint or 0), max_wait_seconds)`. Increment only that class's counter.

- [ ] **Step 1: Failing tests**

```python
async def test_rate_limits_do_not_consume_base_budget():
    # policy(max_attempts=3, backoff=1.0, rate_limit_max_attempts=6, rate_limit_backoff=5.0)
    # op raises 429,429,429,429, then TTS_FAILED, then returns "ok"
    assert result == "ok"
    assert waits == [5.0, 10.0, 20.0, 40.0, 1.0]

async def test_base_errors_do_not_inflate_rate_limit_backoff():
    # TTS_FAILED, TTS_FAILED, then 429, then "ok" → waits == [1.0, 2.0, 5.0]
```

Existing tests in this file and in `test_pipeline.py` must stay green unchanged.
- [ ] **Step 2: Run** `backend/.venv/bin/pytest backend/tests/application/test_retry_rate_limit.py -q`: FAIL. **Step 3: Implement.** **Step 4: Run** the full backend suite: PASS.
- [ ] **Step 5: Commit** `fix: separate retry budgets for rate limits and other errors (#28)`

### Task 3: Non-retryable provider 4xx (T5)

**Files:**
- Modify: `backend/app/providers/tts/http_errors.py` (`map_tts_http_error`)
- Test: `backend/tests/providers/test_fish.py`, `backend/tests/providers/test_vieneu.py` (error-mapping parametrize)

**Interfaces:**
- New mapping. Messages are static strings and never include the response body.

| HTTP | ErrorType | message |
|---|---|---|
| 400, 413, 422 | `INVALID_INPUT` | `"tts provider rejected the request (HTTP {code})"` |
| 404 | `PROVIDER_CONFIGURATION_ERROR` | `"tts voice or endpoint not found (HTTP 404)"` |
| 402 | `PROVIDER_CONFIGURATION_ERROR` | `"tts provider quota or plan limit reached (HTTP 402)"` |
| 401, 403 | `PROVIDER_CONFIGURATION_ERROR` | unchanged |
| 429 | `PROVIDER_RATE_LIMIT` | unchanged |
| other / 5xx / connection | `TTS_FAILED` | unchanged |

- [ ] **Step 1: Failing tests.** Extend the parametrized `test_http_errors` in both provider test files with `(400, INVALID_INPUT)`, `(404, PROVIDER_CONFIGURATION_ERROR)`, `(413, INVALID_INPUT)` and `(422, INVALID_INPUT)`. Add `assert "quota" in err.message` for 402. Add one pipeline-level test (`test_pipeline.py`): a provider raising the 400 mapping fails the chunk after exactly 1 call (no retry).
- [ ] **Step 2: Run, FAIL. Step 3: Implement. Step 4: Run** the full backend suite: PASS. Then `cd frontend && npm test`, to confirm `friendlyError` has copy for these types and no test breaks.
- [ ] **Step 5: Commit** `fix: stop retrying TTS requests the provider rejected (#28)`

### Task 4 (only if the owner picks 503 for T6): engine-unavailable → 503

Default ruling: **keep HTTP 400** (current behaviour, ledger ruling). The frontend keys on `error_type`, not on the status code. Run this task only if the owner chooses 503.

**Files:** `backend/app/api/errors.py`, `backend/app/application/jobs.py` (preflight raise site), `backend/tests/api/test_jobs.py`
**Interfaces:** new `ErrorType.PROVIDER_UNAVAILABLE` → HTTP 503, raised only by the create preflight when `check_available` fails. "Disabled engine" stays `INVALID_INPUT`/400, and other `PROVIDER_CONFIGURATION_ERROR` stays 400. Add the frontend `friendlyError` copy and show "Use Edge instead" for both types.
- [ ] Steps: failing API test (fish without key → 503 `PROVIDER_UNAVAILABLE`) → implement → backend + frontend suites → commit `fix: 503 when a pinned TTS engine is temporarily unavailable (#28)`.

### Task 5: Verify, push, answer the review (outward-facing: owner approves first)

- [ ] **Step 1:** `make check`: all green.
- [ ] **Step 2 (owner approval):** `git push`.
- [ ] **Step 3 (owner approval):** Reply in the Bugbot thread (`gh api repos/teemo2Xteemo/text-to-audiobook/pulls/37/comments/4434783400/replies`) with the fix commit. Post one PR comment that answers T1–T7: the fix commit for each of T1–T5, the T6 decision, and T7 with the evidence from "Out of scope". No gratitude lines.
- [ ] **Step 4 (owner approval):** Open one follow-up issue with the deferred notes listed under "Out of scope".
- [ ] **Step 5:** Update the PR description's "Known issues" (remove the fixed VieNeu backoff item).
