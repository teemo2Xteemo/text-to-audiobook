# Implementation plan

- Status: Accepted
- Date: 2026-09-01
- Scope: MVP (AC-01 … AC-12)
- Does not replace: ADRs, `.cursor/rules`, `target-structure.md`

This document sequences **work**. Stack, layout, ports, async jobs, chunking, CPU-first, and Edge TTS as first TTS adapter are already decided (ADR 0001–0010). Do not re-litigate them here.

Chinese → Vietnamese is the **primary demo fixture**, not a language pair to encode in domain or UI constants.

---

## 1. Summary

Thirteen milestones (roughly one PR each) take the repo from governance-only to an MVP that satisfies AC-01–AC-12.

Order: runnable API+Redis Compose → **domain ports and types with unit tests** → **async jobs (HTTP → Redis/RQ → worker)** before any NLLB/Edge SDK → full pipeline **shape** on fakes (parse → chunk → translate → narrate → TTS → FFmpeg merge → pollable status) → real NLLB (CPU distilled) and Edge TTS → **retry, resume, cache** as near-term MVP milestones (ADR 0007), not a later product phase.

Tests live **in each milestone**. Deferred: EPUB/PDF/URL, Postgres/auth/job history, Piper/XTTS/Chatterbox/ElevenLabs, cloning, multi-speaker, subtitles, video, websockets, CI.

---

## 2. Milestone sequence

```text
M1 scaffold/compose (API+Redis)
    ↓
M2 domain contracts (ports, chunker, cache key, job FSM)
    ↓
M3 job HTTP + FS storage + RQ enqueue
    ↓
M4 pipeline orchestrator (fakes) ─── independently testable
    ↓
M5 worker + DI + GET /api/capabilities + Compose worker
    ↓
M6 frontend (typed client against real API)
    ↓
M7 narration processor (real stage, still fake TTS/NLLB)
    ↓
M8 NLLB adapter (CPU distilled)
    ↓
M9 Edge TTS adapter + FFmpeg normalize
    ↓
M10 chunk retry  →  M11 checkpoint resume  →  M12 translation/TTS cache
    ↓
M13 Compose completeness + AC-09/AC-10/AC demo
```

M4 does not require Redis (application tests). M5 is what makes jobs run. M8/M9 may run in parallel after M5; M9 needs the FFmpeg helper from M4. Do not start M8/M9 before M2 ports exist.

---

### M1 — Scaffold and Compose (API + Redis)

**Depends on:** none  
**Touches layers:** api, config, devops

**Adds/changes:**

- `backend/` per `docs/ai/target-structure.md`: `pyproject.toml` (Python 3.12+, FastAPI, Pydantic settings, pytest, ruff), `app/config`, `app/api` with `GET /health`, API `Dockerfile` (CPU, no CUDA, no secrets).
- Root `docker-compose.yml`: **api + redis** only; healthchecks; `.env.example` (names only).
- `storage/` remains gitignored.

**Explicitly excludes:** worker, frontend, pipeline, provider SDKs, RQ consumption.

**Acceptance check:**

- `docker compose up` brings api + redis; `GET /health` → 200.
- pytest: settings/health smoke (Redis not required for unit defaults).

**Maps to:** AC-09 (CPU image), AC-10 (partial), requirements §24/§25.

**Env (new):** `REDIS_URL`, `STORAGE_PATH`. Assumption: local bind-mount. Alternatives: S3. Recommendation: filesystem (ADR 0003).

**Open:** API vs worker image split — recommend two images from M5; M1 may be slim API only.

---

### M2 — Domain contracts

**Depends on:** M1  
**Touches layers:** domain

**Adds/changes:**

- Types: BCP-47 `source_language` / `target_language` (`auto` allowed for source only), `JobStatus` StrEnum with **lowercase values** (`queued`, `parsing`, `translating`, `preparing_tts`, `generating_audio`, `merging`, `completed`, `failed`) + legal transitions (only non-terminal → `FAILED`; `COMPLETED` and `FAILED` are terminal), `error_type` codes from `.cursor/rules/02-backend.mdc`, `Chunk` (stable ids, e.g. `chunk-001`), `TTSSettings`, `Voice`, `AudioArtifact`.
- Ports (no vendor imports): `TranslationProvider` / `TTSProvider` match `docs/ai/provider-development.md`. `NarrationProcessor` is a pipeline-stage port (ADR 0005): `process(text, language) -> str`. `LanguageDetector`: `detect(text) -> LanguageDetection(language_code, confidence)` (low-confidence threshold is M8). Reuse `backend/app/domain` — do not copy a second interface into the worker.
- Pure functions: unicode **character-budget chunker** (language-agnostic, default `max_chars=1200`), **cache-key** helper (operation + text + languages + provider + model + voice + settings), job transition guard.
- Tests: transitions, illegal transitions, mixed-script chunking, cache miss when `target_language` or voice changes, detector not used when source is explicit.

**Explicitly excludes:** FastAPI, Redis, FFmpeg, `edge_tts`, `transformers`, HTTP schemas.

**Acceptance check:** `pytest backend/tests` (suite includes M1 health/settings plus domain). No vendor SDKs under `app/domain`.

**Maps to:** AC-06, AC-11 (ports exist), ADR 0001/0002/0004/0005/0006.

**Decided:** chunk budget 1200 Unicode chars as a domain parameter — see §4.

---

### M3 — Job HTTP, storage, enqueue

**Depends on:** M2  
**Touches layers:** api, application, infrastructure, config

**Adds/changes:**

- `POST /api/jobs` → **202** `{ job_id, status: queued }`. Multipart **or** JSON paste. Fields: `source_language`, `target_language`; optional `voice`, `speed`, `output_format` (`mp3` / `wav`). Exactly one of `text` or `file`.
- `GET /api/jobs/{job_id}` → `status` and `stage` both serialize `JobStatus` (same string in M3), `chunk_current` / `chunk_total` (zeros until worker), `error_type` / message, no stack traces. Result locator / audio URL is **M5**, not M3.
- Upload rules: `MAX_UPLOAD_BYTES` (default `2000000`) for paste and file, `.txt` / text MIME, sanitized filename, no `../`; write `storage/jobs/{job_id}/source.txt`.
- Redis job record + RQ enqueue (`job_id` only; queue name `jobs`; func path `app.workers.process_job` — callable arrives in M5). API does **not** run translation/TTS.
- Routes call `JobService` only. Domain ports: `JobStore`, `JobQueue`, `SourceTextStorage`. Redis/RQ/FS adapters in `infrastructure/`. Wire in `config/` (M5 extends this factory for providers — do not add a second composition root in `application/`).
- Errors: envelope `{ "error_type", "message" }`; 400 validation; 404 unknown job (`INVALID_INPUT`, no new enum); 503 `STORAGE_FAILED`.
- `TestClient` tests with a fake application service.

**Explicitly excludes:** worker execution, NLLB/Edge, download until audio exists, auth.

**Acceptance check:** invalid file → `INVALID_INPUT`; create → 202 + uuid. Integration optional: POST then GET `queued`.

**Maps to:** AC-01, §4, §19, §29, §36, ADR 0003.

**API decision:** one endpoint for paste (`text`) and `file`. Unversioned paths (`/health`, `/api/jobs`) — no `/v1` prefix; FastAPI `version` is OpenAPI metadata only (see §4). Error envelope and upload conflict rule as above.  
**Auth decision:** no auth in MVP; UUID is the capability; bind localhost in Compose (see §4).  
**Decided:** dual-write `status.json` + Redis; `speed` on create; `OUTPUT_BITRATE_KBPS` / `MAX_UPLOAD_BYTES` — see §4.

**Env (new):** `OUTPUT_BITRATE_KBPS`, `MAX_UPLOAD_BYTES`.

---

### M4 — Pipeline orchestrator with fakes

**Depends on:** M2  
**Touches layers:** application, infrastructure, tests

**Adds/changes:**

- Application pipeline, **one stage module at a time**: parse → chunk → translate → narrate → TTS → normalize → merge.
- Fakes for translation, TTS, narration, language detection. TTS writes tiny fixture bytes under `storage/jobs/{id}/audio/`.
- FFmpeg helper: **argv list only**. Default unit tests use an `AudioProcessor` **port** + fake concatenator; `@pytest.mark.integration` if `ffmpeg` is on PATH.
- Checkpoint after each successful chunk. Idempotent skip if artifact already valid.
- Fake translator threads `target_language` into output so tests prove no hard-coded pair.

**Explicitly excludes:** RQ worker process, real NLLB/Edge, retry backoff, content-addressed cache, frontend.

**Acceptance check:** pytest on a 3-sentence fixture through fakes; output file exists; `chunk_total >= 1`; domain still vendor-free.

**Maps to:** pipeline shape for AC-02/03/05/12; ADR 0004/0005.

---

### M5 — Worker, DI, capabilities, Compose worker

**Depends on:** M3, M4  
**Touches layers:** workers, application (composition root), api, devops

**Adds/changes:**

- RQ worker: load job, call pipeline, update Redis + FS; **bounded concurrency** (default 1).
- Composition root: `TRANSLATION_PROVIDER=fake`, `TTS_PROVIDER=fake`. Name → adapter **only** in config/registry, never in domain/application/routes.
- `GET /api/capabilities`: language intersection of translation + TTS ports; voices filtered by query `language`.
- Compose **worker** (FFmpeg). API image stays without PyTorch.
- Download when `COMPLETED` (`GET /api/jobs/{id}/audio` or equivalent).

**Explicitly excludes:** NLLB weights, Edge network calls, frontend.

**Acceptance check:** Compose api + redis + worker; POST short job using fake capabilities; poll `COMPLETED`; download fixture audio. Capabilities must not hard-code Edge voice IDs in api/application.

**Maps to:** AC-10 (worker), §17–20, AC-11 (factory).

**Env (new):** `TRANSLATION_PROVIDER`, `TTS_PROVIDER`, `WORKER_CONCURRENCY`.

---

### M6 — Frontend job UI

**Depends on:** M5  
**Touches layers:** frontend, devops

**Adds/changes:**

- Vite + React + TypeScript **strict**; typed client; `idle | loading | success | error`.
- Upload or paste; source (incl. Auto); target; voice from capabilities filtered by target; speed; output format; Generate.
- Poll job status; stage + chunk counts; user-facing `error_type`; player on completion.
- Frontend Dockerfile; Compose frontend; `VITE_API_BASE_URL`.
- Tests with mocked `fetch` + capability fixtures — not a hard-coded language/voice table.

**Explicitly excludes:** pipeline logic in the client, Next.js, auth, full job history (sessionStorage of last `job_id` is OK).

**Acceptance check:** paste text, pick languages from API, queued → completed, play audio. `tsc --noEmit` clean.

**Maps to:** §17–18, AC-01/02/04/05 (UI), AC-10.

**Env:** `DEFAULT_SOURCE_LANGUAGE=auto`; optional `DEFAULT_TARGET_LANGUAGE` for UI preselect only — never a TypeScript constant list.

---

### M7 — Narration processor

**Depends on:** M4 (wired); M5 so Compose still runs  
**Touches layers:** application (domain-pure helpers OK)

**Adds/changes:**

- Implement the existing domain `NarrationProcessor` port (`process(text, language) -> str` in `backend/app/domain/ports.py`). Do not define a second protocol.
- Real narration: punctuation, pauses, dialogue, numbers, abbreviations, symbols, quotes — **without changing meaning** (ADR 0005). Conservative; fixtures in multiple scripts, not zh/vi-only.
- Replace fake narrator in DI.
- Tests: no dropped clauses; TTS input ≠ raw translation.

**Explicitly excludes:** vendor SSML, emotion models, LLM rewrite.

**Acceptance check:** pytest pairs; pipeline asserts narration changed structure (breaks/pauses), not vendor TTS quality.

**Maps to:** AC-12, §8.

Start with sentence boundaries, pause/ellipsis, quote cleanup; add number expansion only with tests.

---

### M8 — NLLB translation adapter

**Depends on:** M2, M5  
**Touches layers:** providers, config, devops (worker image)

**Adds/changes:**

- `backend/app/providers/translation/` NLLB adapter: BCP-47 (+ `auto` via `LanguageDetector`) mapped to NLLB codes **inside the adapter**.
- `NLLB_MODEL_ID` env (not a domain constant). Worker may install CPU torch; **API image does not**.
- `TRANSLATION_PROVIDER=nllb` via env/profile; default Compose may stay `fake` for fast boot.
- Unit tests mock the model wrapper; optional `@pytest.mark.integration`.
- `UNSUPPORTED_LANGUAGE` for unsupported pairs.
- CPU `LanguageDetector` adapter. Low confidence → clear error, **do not** assume Chinese.

**Explicitly excludes:** LibreTranslate/OpenAI/Gemini, GPU, 3.3B as default.

**Acceptance check:** mapping + unsupported-pair unit tests. Manual: `zh-CN` → `vi-VN` **and** a second pair the distilled model supports, to prove no hard-coded pair.

**Maps to:** AC-02, AC-09, §7, ADR 0009.

**Env:** `NLLB_MODEL_ID` — recommend `facebook/nllb-200-distilled-600M` (§4).

---

### M9 — Edge TTS adapter + FFmpeg normalize

**Depends on:** M4 FFmpeg helper, M5 DI, M7  
**Touches layers:** providers, infrastructure, config

**Adds/changes:**

- `EdgeTTSProvider`: `voices_for(language)`, `synthesize` with `TTSSettings` (speed; pitch/volume if SDK allows). Voice IDs stay in adapter/config.
- Normalize Edge output with FFmpeg **before** merge.
- `TTS_PROVIDER=edge`; capabilities list real voices for `target_language`.
- Unit tests with mocked Edge client; voice/language mismatch → typed error. Optional live integration marker.

**Explicitly excludes:** Piper/XTTS/ElevenLabs, cloning, a global `vi-VN-*` in domain. Per-language default = first listed voice or env map.

**Acceptance check:** `target_language=vi-VN` → playable MP3 (AC example). Another target → different voice list. Domain/application still do not import `edge_tts`.

**Maps to:** AC-03, AC-04, AC-05, AC-11, §9–12, ADR 0008.

**Env:** `TTS_PROVIDER`; optional `TTS_DEFAULT_VOICE_BY_LANGUAGE`. If unset, first `voices_for(target_language)`.

---

### M10 — Chunk retry

**Depends on:** M4/M5  
**Touches layers:** application, domain, workers

**Adds/changes:**

- Bounded per-chunk retry (count + backoff from config). Retry only failures.
- Fake that fails `chunk-003` then succeeds; assert 001/002/004 not re-synthesized.
- Exhaust → job `FAILED`; completed artifacts kept. Map flakes to `TRANSLATION_FAILED` / `TTS_FAILED` / `PROVIDER_RATE_LIMIT` / `TIMEOUT`.

**Explicitly excludes:** “regenerate entire job” as the only recovery path.

**Acceptance check:** pytest fail-chunk-3. Logs: `job_id`, `chunk_id`, `retry_count` — no full story, no secrets.

**Maps to:** AC-07, §21, ADR 0007.

**Env:** `RETRY_MAX_ATTEMPTS`, `RETRY_BACKOFF_SECONDS` (§4).

---

### M11 — Checkpoint resume

**Depends on:** M10  
**Touches layers:** application, workers, infrastructure

**Adds/changes:**

- On worker start: load checkpoint, skip valid artifacts, continue at first incomplete/failed chunk.
- Test: stop after chunk 2 of 5; re-invoke; fakes show 1–2 not regenerated.
- Optional `POST /api/jobs/{id}/retry` for `FAILED` jobs (same id, reuse checkpoints).

**Explicitly excludes:** distributed locks beyond one worker per job; `CANCELLED`.

**Acceptance check:** pytest resume; manual: stop Compose worker mid-job, start again, job finishes without redoing early chunks.

**Maps to:** AC-08, §31, ADR 0007.

---

### M12 — Translation and TTS cache

**Depends on:** M2 cache-key helper; **prefer after M9** so keys include real provider/model/voice  
**Touches layers:** application, infrastructure

**Adds/changes:**

- Filesystem content-addressed cache (gitignored); separate translation vs TTS keys; full material per ADR 0006.
- Copy or hardlink into `storage/jobs/{id}/`.
- Tests: different `target_language` → miss; identical inputs → hit; speed change → TTS miss.

**Explicitly excludes:** Redis for MP3 blobs; multi-user cache ACL (single-user UUID jobs).

**Acceptance check:** pytest hit/miss; second identical job does not recall fakes/providers.

**Maps to:** §22, ADR 0006.

May be implemented against fakes after M4, but shipping it after M9 is the useful default.

---

### M13 — Compose completeness and MVP acceptance

**Depends on:** M6, M9, M11 (M12 strongly recommended before calling MVP done)  
**Touches layers:** devops, frontend, api

**Adds/changes:**

- Documented `docker compose up`: frontend + api + worker + redis; FFmpeg in worker; **no GPU**.
- Complete `.env.example`; healthchecks; non-root where practical.
- Fixture `.txt` (Chinese example path) under tests/docs — user still selects `zh-CN` / `vi-VN`.
- Confirm a **second** language pair via UI.
- Optional: enable fast ruff/pytest Cursor hooks per `docs/ai/hooks.md`.

**Explicitly excludes:** k8s, CDN, TLS, CI matrix, committing model weights.

**Acceptance check:** §3 table; `docker compose up` from a clean clone with env file.

**Maps to:** AC-09, AC-10, §37 Phase 1, §41.

---

## 3. AC coverage map

| AC | Meaning | Milestone(s) | Deferred? |
| --- | --- | --- | --- |
| AC-01 | Upload `.txt` is readable (Chinese as example) | M3, M6 | No — fixture, not domain rule |
| AC-02 | Translate `source_language` → `target_language` | M2, M4, M8, M6 | No |
| AC-03 | TTS of target-language text (Vietnamese as example) | M4, M9 | No — `synthesize(language=target)` |
| AC-04 | Voice compatible with target (Vietnamese as example) | M5, M6, M9 | No — no universal vi voice in domain |
| AC-05 | Playable MP3 | M4, M9, M6 | No |
| AC-06 | Language-agnostic chunking | M2; scale in M8/M9 | No |
| AC-07 | One chunk fail does not destroy the job | M10 | No (MVP per ADR 0007) |
| AC-08 | Resume | M4 checkpoints + M11 | No |
| AC-09 | No GPU | M1, M5, M8, M13 | No |
| AC-10 | `docker compose up` | M1, M5, M6, M13 | No |
| AC-11 | Replace Edge TTS without rewriting business logic | M2, M5, FakeTTS + EdgeTTS | No Piper required to prove this |
| AC-12 | Narration rhythm, not raw translation | M7 | No — tested transforms, not YouTube match |

Not in AC scope: §32 quality dashboard, §33 YouTube analysis, Phase 2–4 features.

**MVP done:** M13 checklist green — Compose on CPU; paste/upload; languages/voices from capabilities; playable MP3; language-agnostic chunks; retry + resume; Edge replaceable at adapter/factory; narration is a separate tested stage.

---

## 4. Open decisions

Resolve with Assumption / Impact / Alternatives / Recommendation before or during the named milestone. Do not silently invent product behavior. Items marked **decided** are locked; do not re-open them.

### Chunk budget (M2) — decided

- **Decided (M2):** Domain chunker default `max_chars=1200` Unicode characters. Pack paragraphs then sentences; oversize units hard-split by character with ids `chunk-00N` (no suffix in domain). Not an env/`Settings` field in M2 (domain config ≠ application config).
- Adapter may further split with suffix ids (`chunk-003.2`) only if needed — then a short ADR.

### API URL version (M1, M3) — decided

- **Decided:** Unversioned paths as already specified: `GET /health`, `POST /api/jobs`, `GET /api/jobs/{job_id}`, `GET /api/capabilities`. No `/api/v1` (or other version segment). FastAPI app `version` (e.g. `0.1.0`) is OpenAPI metadata only, not a URL prefix.
- Revisit when there is a second public consumer or a breaking API; do not add `/v1` in MVP.

### NLLB model (M8)

- **A:** `NLLB_MODEL_ID=facebook/nllb-200-distilled-600M`.
- **I:** Quality vs RAM; first run downloads into a volume.
- **Alt:** 1.3B distilled; cloud translator.
- **R:** 600M default; never 3.3B in default Compose (ADR 0009).

### `auto` source (M6, M8)

- **A:** `LanguageDetector` port; CPU adapter; no “assume zh”.
- **I:** Mis-detect → bad translation.
- **Alt:** hide Auto until detector exists.
- **R:** Auto in UI; low confidence → user must set source.

### Job metadata (M3) — decided

- **Decided (M3):** Dual-write; `storage/jobs/{id}/status.json` is resume source of truth; Redis is the GET cache (extends ADR 0003). Redis flush alone must not make resume impossible.

### Retry (M10)

- **A:** `RETRY_MAX_ATTEMPTS=3`, backoff 1s / 2s / 4s.
- **I:** Latency vs Edge rate limits.
- **R:** Configurable via env.

### Images (M1, M5)

- **A:** Slim API; worker has torch + ffmpeg + edge-tts.
- **Alt:** one image, never load NLLB in API process.
- **R:** Two images.

### Compose default providers (M5, M13)

- **A:** Default `fake` / `fake` so `compose up` works offline.
- **I:** Demo needs documented override.
- **R:** Fakes default; `.env.example` describes nllb+edge profile.

### UI defaults (M6)

- **A:** No domain default voice/language.
- **Alt:** `.env.example` sets `DEFAULT_TARGET_LANGUAGE=vi-VN` for the operator demo.
- **R:** Env-driven preselect if set; never TS/Python literals for zh/vi.

### Auth (M3, M13) — decided

- **Decided (M3):** No auth in MVP; UUID is the capability; Compose binds localhost (`127.0.0.1`). Auth when public (§36).

### Bitrate / speed (M3, M9) — decided

- **Decided (M3):** `speed` on `POST /api/jobs` (float, default `1.0`, range `0.5`–`2.0`); `OUTPUT_BITRATE_KBPS` env default `128`, not a request field. Do not offer 320 in MVP UI. No `CANCELLED` state in MVP. M9 must not change this public API.

---

## 5. Risks

| Risk | Mitigation in this sequence |
| --- | --- |
| NLLB quality / RAM | Fakes first (M4–M6); model only in worker (M8); concurrency 1. |
| Edge TTS ToS / rate limits / outages | FakeTTS + port; retry M10; swap adapter without job rewrite. |
| AC-03/04 vs multilingual rules | zh/vi fixtures; capabilities-driven voices; hooks flag Neural IDs in domain/UI. |
| OOM from parallel chunks | `WORKER_CONCURRENCY=1` from M5. |
| Sync HTTP prototype | No `/generate`; jobs from M3 before real TTS. |
| Resume retrofit | Checkpoints in M4; FS `status.json`; M11 proves restart. |
| FFmpeg injection | Argv helper in M4; job-scoped paths only. |
| Narration over-edit | Conservative M7 tests before paid/slow TTS. |
| Heavy first `compose up` | Fake providers by default. |
| Agent encodes zh→vi | M2 tests + always-apply rules; capabilities before UI. |

---

## 6. Not doing now

Justified by requirements Phase 2–4 (§37), ADR 0008/0009, ADR 0003.

- EPUB, DOCX, PDF, URL ingest
- Chapter UX, persisted job history, extra preview gallery (current-job player is in M6)
- Piper / XTTS / Chatterbox / ElevenLabs / Azure / Google TTS (Fake + Edge satisfy AC-11)
- Voice cloning, multi-speaker, emotion, subtitles
- Images / video / YouTube export
- YouTube reference analysis (§33)
- Postgres, user accounts, end-user API keys
- Websockets
- Kubernetes / production TLS
- GitHub Actions CI as a blocking milestone
- NLLB weights in git or the API image
- `CANCELLED`, multi-tenant ownership
- LLM translation providers

---

## Implementing a milestone

Follow `docs/ai/coding-workflow.md`: read rules + matching ADRs → smallest change → unit tests with fakes → formatter → diff for secrets and language hard-coding.

Do not implement M8–M12 when asked only for M1. Do not add folders outside `docs/ai/target-structure.md` without an ADR.
