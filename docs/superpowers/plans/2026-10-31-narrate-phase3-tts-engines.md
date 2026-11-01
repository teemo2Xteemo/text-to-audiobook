# Narrate Phase 3 — Multilingual TTS Engines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each job pick a TTS engine (`edge` | `vieneu` | `fish`) so mixed vi/en(/zh) text is read by one multilingual voice, without ever switching engines silently.

**Architecture:** Two new adapters behind the existing `TTSProvider` port (`domain/ports.py`). A per-job pin `Job.tts_engine` mirrors the translation pin: the worker applies it with `settings_for_tts_pin()`, which rewrites `settings.tts_provider`, so `build_tts_provider`, `build_audio_processor` and `cache_identity_from_settings` all follow the pin with no other wiring. Rate limits flow through the existing `with_chunk_retry` with a separate, longer policy for `PROVIDER_RATE_LIMIT`.

**Tech Stack:** Python 3.12, FastAPI, urllib (no new HTTP dependency), pytest; React + Vitest; Docker Compose.

**Spec:** GitHub issue #28 §9 (authoritative for Phase 3) and `docs/superpowers/specs/2026-10-28-narrate-mode-design.md`.

**Out of scope (and why):**
- Phase 2 per-chapter assembly / ZIP: blocked on #29.
- "Continue with Edge" mid-job re-pin (§9.6 c) and `TTS_FALLBACK_ENGINE`/`TTS_FALLBACK_SCOPE`: the spec ties the switch to #29's per-chapter manifest, and the fallback defaults to `off`. Revisit with #29.
- Changing any default engine: only after the listening pilot (Task 8).

## Global Constraints

- Edge stays the default and `TTS_PROVIDER` semantics are unchanged; a job without `tts_engine` behaves exactly as today.
- Never switch engine without a user action. No code path may call a different `TTSProvider` than the job's pinned one.
- Voice ids are namespaced: `vieneu:<preset>`, `fish:<reference_id>`. Edge ids unchanged.
- Fish: always send header `model: $FISH_AUDIO_MODEL` (default `s2.1-pro-free`). Missing header bills paid `s2.1-pro`.
- Error mapping (all new adapters): `429` → `PROVIDER_RATE_LIMIT` (with `retry_after_seconds` when a header exists); `503`/connection error → `TTS_FAILED`; timeout → `TIMEOUT`; `401`/`402`/`403` → `PROVIDER_CONFIGURATION_ERROR`.
- Rate-limit retry: `RETRY_RATE_LIMIT_MAX_ATTEMPTS=6`, `RETRY_RATE_LIMIT_BACKOFF_SECONDS=5`, each wait capped at 120 s.
- Defaults: `VIENEU_MAX_CONCURRENCY=1`, `VIENEU_HTTP_TIMEOUT_SECONDS=300`, `VIENEU_PRECISION=fp32`, `FISH_AUDIO_MAX_CONCURRENCY=2`, `FISH_AUDIO_HTTP_TIMEOUT_SECONDS=120`, `FISH_AUDIO_BASE_URL=https://api.fish.audio`.
- Cache TTS model identity: `edge` → `"edge"`, `fake` → `"fake"`, `fish` → `FISH_AUDIO_MODEL`, `vieneu` → `f"vieneu-v3-turbo:{VIENEU_PRECISION}"`.
- Every new setting goes into `.env.example` (enforced by `backend/tests/test_env_example.py`). No secrets committed.
- `Job.to_dict` writes `tts_engine` only when set; old `status.json` files stay byte-identical.

## Review Focus

1. **Fish enabled but `FISH_AUDIO_API_KEY` empty** → `GET /api/capabilities` still returns 200 with `fish.available=false`; creating a fish job returns 400 `PROVIDER_CONFIGURATION_ERROR`. Test in Task 6.
2. **Voice from another engine** (`voice="fish:abc"`, `tts_engine="edge"`, or `vieneu:*` with no pin) → 400 `UNSUPPORTED_LANGUAGE` at create, never a worker failure. Test in Task 5.
3. **Fish 429 without `Retry-After`, repeatedly** → total wait is bounded (6 attempts, each ≤120 s), then the job is `FAILED` with `PROVIDER_RATE_LIMIT` and checkpoints are kept; Edge is never called. Test in Task 5.
4. **Text that is only brackets** (`"[ ]"`, `"【】"`) → after sanitizing nothing is left, so the adapter raises `INVALID_INPUT` and never sends an empty request. Test in Task 3.
5. **Old job without `tts_engine` after the operator changes `TTS_PROVIDER`** → it uses the live setting (today's behaviour), and the cache identity for edge/fake is unchanged so existing cached audio is still hit. Test in Task 2 and Task 5.

---

### Task 1: Shared Retry-After helper + rate-limit retry policy

**Files:**
- Create: `backend/app/providers/http_retry.py`
- Modify: `backend/app/providers/translation/openai_compatible.py` (delete `_parse_retry_after`, `_retry_after_http_date`, `_retry_after_header`; import from `http_retry`)
- Modify: `backend/app/domain/retry.py`, `backend/app/application/pipeline/retry.py`, `backend/app/config/settings.py`, `backend/app/config/factory.py:357`, `.env.example`
- Test: `backend/tests/providers/test_http_retry.py`, `backend/tests/domain/test_retry.py`, `backend/tests/application/test_pipeline.py`

**Interfaces:**
- Produces: `parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None` and `retry_after_header(exc: urllib.error.HTTPError) -> str | None` in `app/providers/http_retry.py` (same behaviour as today's private versions, including `MAX_RETRY_AFTER_SECONDS` cap).
- Produces: `RetryPolicy(max_attempts, backoff_seconds, rate_limit_max_attempts: int = 6, rate_limit_backoff_seconds: float = 5.0, max_wait_seconds: float = 120.0)`.

- [ ] **Step 1: Move the existing retry-after tests.** Any tests in `test_openai_compatible_translation.py` that call `_parse_retry_after`/`_retry_after_header` move to `test_http_retry.py` and import the public names. Run them: FAIL (module missing).
- [ ] **Step 2: Create `http_retry.py`** by moving the three functions (public names) and the cap constant; `openai_compatible.py` imports them. Run `backend/.venv/bin/pytest backend/tests/providers -q`: PASS.
- [ ] **Step 3: Write failing retry tests**

```python
async def test_rate_limit_uses_rate_limit_policy():
    waits = []
    policy = RetryPolicy(max_attempts=3, backoff_seconds=1.0,
                         rate_limit_max_attempts=6, rate_limit_backoff_seconds=5.0)
    op = always_raise(DomainError(ErrorType.PROVIDER_RATE_LIMIT, "busy"))
    with pytest.raises(DomainError):
        await with_chunk_retry(op, policy=policy, sleep=record(waits),
                               job_id="j", chunk_id="c", stage="tts")
    assert waits == [5.0, 10.0, 20.0, 40.0, 80.0]

async def test_retry_after_hint_capped():
    ...  # DomainError(PROVIDER_RATE_LIMIT, retry_after_seconds=500) → first wait == 120.0

async def test_other_errors_keep_base_policy():
    ...  # TTS_FAILED forever → waits == [1.0, 2.0]
```

- [ ] **Step 4: Implement.** `with_chunk_retry` picks `(rate_limit_max_attempts, rate_limit_backoff_seconds)` when the caught error is `PROVIDER_RATE_LIMIT`, else the base pair; wait = `min(max(backoff * 2**attempt, hint or 0), max_wait_seconds)`. Settings: `retry_rate_limit_max_attempts: int = Field(default=6, ge=1)`, `retry_rate_limit_backoff_seconds: float = Field(default=5.0, ge=0.0)`; wire both in `factory.py` and add to `.env.example`.
- [ ] **Step 5: Run** `backend/.venv/bin/pytest backend/tests -q`: PASS.
- [ ] **Step 6: Commit** `refactor: shared retry-after helper and rate-limit retry policy (#28)`

### Task 2: Engine-specific cache identity

**Files:**
- Modify: `backend/app/config/factory.py::cache_identity_from_settings`, `backend/app/config/settings.py`
- Test: `backend/tests/config/test_factory.py`, `backend/tests/domain/test_cache_keys.py`

**Interfaces:**
- Produces: `tts_model_identity(settings: Settings) -> str` in `factory.py`; `cache_identity_from_settings` uses it for `tts_model`.
- Produces settings (also used by Tasks 3–4): `fish_audio_model: str = "s2.1-pro-free"`, `vieneu_precision: Literal["fp32", "int8"] = "fp32"`.

- [ ] **Step 1: Failing test**

```python
@pytest.mark.parametrize("provider,extra,expected", [
    ("edge", {}, "edge"),
    ("fake", {}, "fake"),
    ("fish", {"fish_audio_model": "s2.1-pro-free"}, "s2.1-pro-free"),
    ("vieneu", {"vieneu_precision": "int8"}, "vieneu-v3-turbo:int8"),
])
def test_tts_model_identity(provider, extra, expected):
    assert tts_model_identity(Settings(tts_provider=provider, **extra)) == expected
```

Plus: two `CacheIdentity`s that differ only in vieneu precision produce different `build_cache_key` values for the same text/voice.
- [ ] **Step 2: Run, FAIL. Step 3: Implement. Step 4: Run, PASS** (edge/fake unchanged ⇒ existing cache entries still hit).
- [ ] **Step 5: Commit** `feat: per-engine TTS cache identity (#28)`

### Task 3: Fish Audio adapter + shared TTS HTTP helpers + bracket sanitizing

**Files:**
- Create: `backend/app/providers/tts/http_errors.py`, `backend/app/providers/tts/brackets.py`, `backend/app/providers/tts/fish.py`
- Modify: `backend/app/config/settings.py`, `backend/app/config/factory.py::build_tts_provider`, `.env.example`
- Test: `backend/tests/providers/test_fish.py`, `backend/tests/providers/test_tts_brackets.py`

**Interfaces:**
- Consumes: `parse_retry_after`, `retry_after_header` (Task 1); `fish_audio_model` setting (Task 2).
- Produces: `map_tts_http_error(exc: Exception) -> DomainError` (mapping per Global Constraints; 429 keeps `retry_after_seconds=None` when no header, so the retry policy backs off).
- Produces: `sanitize_brackets(text: str) -> str` — `[x]`, `【x】`, `〔x〕` → `(x)`; empty brackets removed; whitespace collapsed.
- Produces: `FishTTSProvider(output_dir, *, api_key, base_url, model, voices: Sequence[Voice], timeout_seconds, max_concurrency, opener=None)`, `max_concurrency` attribute, `parse_fish_voices(value: str) -> list[Voice]`, `async def check_available(self) -> None` (raises `ProviderConfigurationError` when `api_key` is empty).
- Settings: `fish_audio_api_key: SecretStr = ""`, `fish_audio_base_url`, `fish_audio_voices: str = ""`, `fish_audio_http_timeout_seconds: float = 120`, `fish_audio_max_concurrency: int = 2`.

- [ ] **Step 1: Failing tests** (fake the HTTP opener the same way `test_openai_compatible_translation.py` does)

```python
def test_parse_fish_voices_multi_language():
    voices = parse_fish_voices("vi-VN=ref1:Ngọc Lan,en-US=ref1:Narrator")
    assert [(v.id, v.language, v.label) for v in voices] == [
        ("fish:ref1", "vi-VN", "Ngọc Lan"), ("fish:ref1", "en-US", "Narrator")]

async def test_request_shape(fake_opener):
    await provider.synthesize("Xin chào [System] OK", "vi-VN", "fish:ref1", TTSSettings(speed=1.2))
    req = fake_opener.last
    assert req.full_url == "https://api.fish.audio/v1/tts"
    assert req.get_header("Model") == "s2.1-pro-free"
    assert req.get_header("Authorization") == "Bearer k"
    body = json.loads(req.data)
    assert body["text"] == "Xin chào (System) OK"
    assert body["reference_id"] == "ref1"
    assert body["format"] == "mp3" and body["mp3_bitrate"] == 128 and body["normalize"] is True
    assert body["prosody"] == {"speed": 1.2}
    assert "language" not in body

@pytest.mark.parametrize("code,etype", [
    (401, ErrorType.PROVIDER_CONFIGURATION_ERROR), (402, ErrorType.PROVIDER_CONFIGURATION_ERROR),
    (429, ErrorType.PROVIDER_RATE_LIMIT), (503, ErrorType.TTS_FAILED)])
async def test_http_errors(code, etype): ...
async def test_429_with_retry_after_header(): ...       # retry_after_seconds == 7.0
async def test_timeout_maps_to_timeout(): ...
async def test_unknown_voice_raises_unsupported_language(): ...
async def test_bracket_only_text_does_not_call_api(): ...  # Review Focus 4: INVALID_INPUT, opener not called
```

- [ ] **Step 2: Run** `backend/.venv/bin/pytest backend/tests/providers/test_fish.py backend/tests/providers/test_tts_brackets.py -v`: FAIL.
- [ ] **Step 3: Implement.** Do the urllib call inside `asyncio.to_thread`, as `openai_compatible.py` does. Write the mp3 bytes to `output_dir/<uuid4>.mp3` and return `AudioArtifact` the same way `EdgeTTSProvider` does. Register `"fish"` in `build_tts_provider`. Edge never calls `sanitize_brackets`.
- [ ] **Step 4: Run, PASS. Step 5: Commit** `feat: Fish Audio TTS adapter (#28)`

### Task 4: VieNeu-TTS adapter + optional Compose sidecar

**Files:**
- Create: `backend/app/providers/tts/vieneu.py`
- Modify: `backend/app/config/settings.py`, `backend/app/config/factory.py::build_tts_provider`, `docker-compose.yml`, `.env.example`, `README.md` (one short section)
- Test: `backend/tests/providers/test_vieneu.py`

**Interfaces:**
- Consumes: `map_tts_http_error`, `sanitize_brackets` (Task 3); `vieneu_precision` (Task 2).
- Produces: `VieneuTTSProvider(output_dir, *, base_url, model, timeout_seconds, max_concurrency=1, opener=None)`. Voices come from `GET {base_url}/voices` (fetched lazily and cached) as `Voice(id=f"vieneu:{name}", language="vi-VN")`. `languages()` returns `{"vi-VN"}`. `check_available()` calls `GET {base_url_root}/health` and raises `ProviderConfigurationError` on failure.
- Settings: `vieneu_base_url: str = "http://vieneu:8000/v1"`, `vieneu_model: str = "vieneu-v3-turbo"`, `vieneu_http_timeout_seconds: float = 300`, `vieneu_max_concurrency: int = 1`. `VIENEU_PRECISION` and `VIENEU_MODEL_DIR` configure the sidecar container.

- [ ] **Step 0: Pin the upstream contract.** Read the upstream VieNeu-TTS server (`https://github.com/pnnbao97/VieNeu-TTS`, `docker/docker-compose.yml` `api-cpu` profile). Record in the test module docstring:
  - the exact JSON shape of `GET /v1/voices`;
  - the `POST /v1/audio/speech` body fields (expected OpenAI shape: `model`, `input`, `voice`, `response_format`, `speed`);
  - whether `speed` is honoured.
  
  If `speed` is not honoured, the adapter sends `speed` anyway. A separate test asserts that the orchestrator path calls `AudioProcessor.adjust_tempo` when `settings.speed != 1.0`. That is the only case where adapter code may depend on a flag: add the class attribute `honours_speed = False`.
- [ ] **Step 1: Failing tests:**
  - voices parsed and namespaced;
  - the request body contains the bare preset name (no `vieneu:` prefix) and the bracket-sanitized `input`;
  - the response is written as `.wav`;
  - `languages() == {"vi-VN"}`;
  - `max_concurrency == 1`;
  - the error mapping cases from Task 3;
  - a failing `/health` call makes `check_available` raise.
- [ ] **Step 2: Run, FAIL. Step 3: Implement + register `"vieneu"`.** Compose: add a `vieneu` service under `profiles: ["vieneu"]` so the default `docker compose up` is unchanged. It uses the upstream CPU image and the env `VIENEU_PRECISION` (default `fp32`), with `VIENEU_MODEL_DIR` mounted on a named volume.
- [ ] **Step 4: Run** the tests, then `docker compose config -q`: PASS. **Step 5: Commit** `feat: VieNeu-TTS adapter and optional sidecar (#28)`

### Task 5: Per-job engine pin

**Files:**
- Modify: `backend/app/domain/jobs.py`, `backend/app/application/jobs.py`, `backend/app/api/schemas.py`, `backend/app/api/parse.py`, `backend/app/config/settings.py`, `backend/app/config/factory.py` (`build_job_service`, new `settings_for_tts_pin`, `build_tts_engines`), `backend/app/workers/runner.py:95`, `.env.example`
- Test: `backend/tests/domain/test_job_record.py`, `backend/tests/application/test_create_job.py`, `backend/tests/api/test_jobs.py`, `backend/tests/workers/test_process_job.py`, `backend/tests/config/test_factory.py`

**Interfaces:**
- Produces: `Job.tts_engine: str | None = None` (written to `to_dict` only when set); `CreateJobCommand.tts_engine: str | None = None`; `tts_engine` in the JSON and multipart create bodies and in `JobStatusResponse`.
- Produces: `settings_for_tts_pin(settings: Settings, job: Job) -> Settings`, which returns `settings.model_copy(update={"tts_provider": job.tts_engine})` when the job is pinned and `settings` unchanged otherwise.
- Produces: `enabled_tts_engines(settings) -> list[str]`. It parses `TTS_ENGINES_ENABLED` (a comma list, default `""`), lowercases and dedupes the names, and always includes `settings.tts_provider` first.
- Produces: `build_tts_engines(settings) -> dict[str, TTSProvider | None]`, built from `enabled_tts_engines(settings)`. An engine's value is `None` when building it raises `ProviderConfigurationError`.
- `JobService.__init__` gains `tts_engines: Mapping[str, TTSProvider | None] | None = None`. When it is `None`, the service falls back to `{default: tts}`, so existing tests keep passing.

- [ ] **Step 1: Write the failing tests.**
  - Round-trip `tts_engine`. Old snapshots have no `tts_engine` key and load it as `None` (Review Focus 5).
  - Create with `tts_engine="fish"`:
    - persists the engine;
    - resolves the voice against the fish provider;
    - stores the namespaced voice id.
  - Create with `tts_engine` not in the enabled list → `INVALID_INPUT`.
  - Create with a `fish:*` voice and no pin, or with pin `edge` → `UNSUPPORTED_LANGUAGE` (Review Focus 2).
  - API: multipart and JSON carry the same `tts_engine`.
  - Worker: the runner builds its orchestrator from `settings_for_translation_pin(settings_for_tts_pin(settings, job), job)`.
  - Worker test with Fish raising `PROVIDER_RATE_LIMIT` forever:
    - the job ends `FAILED/PROVIDER_RATE_LIMIT`;
    - checkpoints exist for the chunks that finished before the failure;
    - a spy Edge provider records zero calls (Review Focus 3).
- [ ] **Step 2: Run, FAIL. Step 3: Implement. Step 4: Run** `backend/.venv/bin/pytest backend/tests -q`: PASS.
- [ ] **Step 5: Commit** `feat: per-job TTS engine pin (#28)`

### Task 6: Capabilities `tts_engines`, preflight at create, engine-aware preview

**Files:**
- Modify: `backend/app/application/capabilities.py`, `backend/app/api/capabilities.py`, `backend/app/api/schemas.py`, `backend/app/application/jobs.py`, `backend/app/api/jobs.py` (`tts_preview`, `get_tts_provider` dependency), `backend/app/config/factory.py::build_capabilities_service`
- Test: `backend/tests/api/test_capabilities.py`, `backend/tests/application/test_create_job.py`, `backend/tests/api/test_jobs.py`

**Interfaces:**
- Consumes: `build_tts_engines` and `enabled_tts_engines` (Task 5); `check_available()` on the Fish and VieNeu providers (Tasks 3–4).
- Produces: a `TtsEngineInfo` dataclass in `application/capabilities.py` with these fields:
  - `id: str`
  - `label: str`
  - `kind: Literal["online", "local"]`
  - `cost_label: str`
  - `available: bool`
  - `languages: list[str]`
  - `max_concurrency: int`

  Static metadata for each engine:

  | Engine | `label` | `kind` | `cost_label` |
  |---|---|---|---|
  | `edge` | `"Edge"` | online | `"Free"` |
  | `fish` | `"Fish Audio"` | online | `"Free · fair use"` |
  | `vieneu` | `"VieNeu-TTS"` | local | `"Local · CPU"` |
  | `fake` | `"Fake"` | local | `"Free"` |

- `Capabilities` gains `tts_engines: list[TtsEngineInfo]`.
  - `voices` becomes the union of voices from all available engines.
  - The API voice schema gains `engine: str`.
  - Top-level `languages` and `narrate_languages` stay computed from the default engine, so they don't change for existing clients.
- `TTSPreviewRequest` gains `tts_engine: str | None`.
- Preflight: `JobService.create` awaits the pinned provider's `check_available()` when the provider has one. A `None` provider or a failed check raises `DomainError(PROVIDER_CONFIGURATION_ERROR, "<Engine label> is not available right now")`.

- [ ] **Step 1: Write the failing tests.**
  - Fish enabled with no key → capabilities returns 200 and reports `fish.available is False` (Review Focus 1).
  - A fish create with no key → 400 `PROVIDER_CONFIGURATION_ERROR`.
  - VieNeu create while `/health` fails → 400 with the same error type.
  - Preview with `tts_engine="fish"` synthesizes through the fish provider. It also writes to and reads from `PipelineArtifactCache`, so a second identical call makes no HTTP request.
  - Preview with a disabled engine → 400 `INVALID_INPUT`.
- [ ] **Step 2: Run, FAIL. Step 3: Implement. Step 4: Run, PASS. Step 5: Commit** `feat: tts_engines capability, create preflight, engine-aware preview (#28)`

### Task 7: Frontend engine picker

**Files:**
- Modify:
  - `frontend/src/api/types.ts`
  - `frontend/src/api/client.ts`
  - `frontend/src/components/JobForm.tsx`
  - `frontend/src/lib/jobUi.ts` (`voicesForTarget`, `friendlyError`)
- Test:
  - `frontend/src/components/JobForm.test.tsx`
  - `frontend/src/api/client.test.ts`
  - `frontend/src/lib/jobUi.test.ts`

**Interfaces:**
- Consumes: `tts_engines` and `Voice.engine` from capabilities (Task 6), and `tts_engine` on create and preview (Tasks 5–6).
- Produces: `TtsEngine` type; `Capabilities.tts_engines?: TtsEngine[]`; `Voice.engine?: string`; `CreateJobInput.ttsEngine?: string`.

- [ ] **Step 1: Failing tests:**
  - When `tts_engines.length > 1`, a `StudioSelect` labelled "Voice engine" appears, and each option shows its `cost_label`. Unavailable engines are disabled, with the hint "Not configured". With one engine the picker is hidden.
  - Switching the engine filters the voice list to `voice.engine === engine` and resets the voice when the current one doesn't belong to the new engine.
  - `createJob` sends `tts_engine` in both the JSON and FormData branches, but only when it's not the default. Preview sends `tts_engine`.
  - Create fails with `PROVIDER_CONFIGURATION_ERROR` and a non-edge engine selected → the form shows a "Use Edge instead" button. Clicking it sets the engine to `edge` and does **not** resubmit.
  - `friendlyError("PROVIDER_RATE_LIMIT", …)` → "The voice service is limiting free usage right now. Try again later or switch to Edge."
  - The last selected engine is kept in `localStorage`, read in a try/catch, the same way as the last mode.
- [ ] **Step 2: Run** `cd frontend && npm test -- JobForm client jobUi`: FAIL. **Step 3: Implement.** **Step 4: Run** `npm test && npm run typecheck`: PASS.
- [ ] **Step 5: Commit** `feat(frontend): TTS engine picker with cost labels (#28)`

### Task 8: Listening pilot + ADR 0012 (manual, owner-run)

**Files:**
- Create: `docs/adr/0012-multilingual-tts-engines.md`
- Create: `backend/tests/fixtures/narrate-mixed-vi-en.txt`. It is one representative chapter with Sino-Vietnamese names, English terms, numbers and dates, `[System]` and `【…】` brackets, and dialogue.

- [ ] **Step 1:** Add the fixture. Update `tests/test_sample_fixtures.py` if it lists fixtures.
- [ ] **Step 2 (owner):** Synthesize the chapter with:
  - Edge (`vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural`);
  - VieNeu fp32 (int8 only on a VNNI CPU, not on the i5-10300H);
  - Fish `s2.1-pro-free` with 1–2 reference voices.
  
  Use `TTS_ENGINES_ENABLED=edge,vieneu,fish` and one narrate job per engine.
- [ ] **Step 3 (owner):** Blind A/B. Score:
  - names;
  - English code-switching;
  - numbers;
  - prosody;
  - long-form consistency;
  - artifacts.
  
  Record:
  - wall time and RTF on the real host;
  - 429 and error counts;
  - estimated cost per 100 h.
- [ ] **Step 4:** Write ADR 0012 with the results and the per-language default decision. If that decision changes a default, it gets its own one-line commit after the ADR is merged. **Edge stays the default until then.**
- [ ] **Step 5: Commit** `docs: ADR 0012 multilingual TTS engines pilot (#28)`

### Final verification

- [ ] Run `make check` (lint, tests, and the security/policy scan): all green.
- [ ] Offline smoke test:
  1. `make up-build`. The default stack has no vieneu container.
  2. Create a narrate job with no `tts_engine`. It completes as before.
- [ ] Tick the Phase 3 boxes in issue #28 §13 and §14 only for what has been verified.
