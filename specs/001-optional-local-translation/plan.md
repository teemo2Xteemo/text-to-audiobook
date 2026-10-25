# Implementation Plan: Optional Local Literary Translation

**Branch**: `001-optional-local-translation` | **Date**: 2026-10-01 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-optional-local-translation/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

Operators can turn on a local literary translator (Ollama calling TranslateGemma) without a paid account. In the spec, the packaged default is the offline stand-in (`fake`). The free on-device translator is NLLB, used only when the operator selects it. New stories take one lab-wide provider and model from API settings. Each story keeps that pair for its whole life, including retries. The spec calls that recorded pair the pin. A later lab change cannot mix two translators into one audiobook. The adapter itself already exists; this plan stores the pin and uses it for the worker and the translation cache.

## Technical Context

**Language/Version**: Python 3.12+ (backend), TypeScript (status type only)

**Primary Dependencies**: FastAPI, Pydantic settings, existing `TranslationProvider` port, existing Ollama HTTP adapter (stdlib client), Redis/RQ worker

**Storage**: Job `status.json` plus the existing filesystem translation cache. Redis remains the queue and status cache, not the translation blob store.

**Testing**: pytest, marker `not integration` for the default suite. Fake HTTP client for the adapter. Optional live translator check stays manual.

**Target Platform**: Local lab via Docker Compose or a host process. CPU-only. No GPU requirement.

**Project Type**: Web service (API + worker) with an existing React client

**Performance Goals**: No new latency target. Literary calls stay inside the existing queue timeout and `OLLAMA_HTTP_TIMEOUT_SECONDS`.

**Constraints**: Story text goes only to the operator-run translator address. One heavy translator constructed per job run. Default concurrency 1. No model weights in the image. No translator field on create. Job state machine unchanged. Language pairs stay out of domain and application code.

**Scale/Scope**: Single-operator lab. The delta is job identity and worker wiring, not a new pipeline stage.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Gate | Result |
| --- | --- |
| I. Language-agnostic | Pass. The pin stores a provider id and model id from settings. Source and target languages stay on the job as they do today. Demo pairs stay in samples and adapter tests only. |
| II. Ports and boundaries | Pass. Application code records and reads the pin. The factory remains the only place that constructs `OllamaTranslationProvider` or `NllbTranslationProvider`. The orchestrator still calls `TranslationProvider`. |
| III. Durable, async, chunked | Pass. HTTP still returns `job_id`. The worker still runs the pipeline. Chunks, retry, and resume stay. No new job state. Successful pieces are not regenerated when the pin is unchanged. |
| IV. Security and privacy | Pass. The create body stays untrusted and does not gain a translator URL. Secrets and story text stay out of logs. The address stays configuration. |
| V. Evidence-based gates | Pass. Pin behavior is covered with fakes. Live model checks are optional and unmarked as a CI requirement. |

## Project Structure

### Documentation (this feature)

```text
specs/001-optional-local-translation/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/           # Phase 1
│   ├── lab-translator.md
│   └── job-status.md
└── tasks.md             # Phase 2 (/speckit-tasks; not created here)
```

### Source Code (repository root)

```text
backend/app/domain/jobs.py                          # Job pin fields and status.json round-trip
backend/app/application/jobs.py                     # Stamp pin at create; preserve it on retry and failure
backend/app/config/factory.py                       # Build translator and cache identity from a pin
backend/app/application/pipeline/artifact_cache.py  # Cache identity already carries provider + model
backend/app/workers/runner.py                       # Honor the job pin when present
backend/app/api/schemas.py                          # Status response fields
frontend/src/api/types.ts                           # Accept the additive status fields
backend/tests/                                           # Pin, retry, legacy file, exclusive construction
```

**Structure Decision**: Extend the existing backend job and worker path. The Ollama adapter under `backend/app/providers/translation/ollama.py` stays as it is. The React app does not gain a translator control; its status type only accepts the new fields.

## Complexity Tracking

No constitution violations require justification.

## Phase 0 — Research

See [research.md](./research.md). No open clarifications. The gap against the spec is the live worker environment: `process_job` builds the orchestrator from current settings, so an unfinished story follows a later lab choice and cache keys move with it.

## Phase 1 — Design

### Behavior

1. At `JobService.create`, copy the lab provider id and model id onto the job before save. The HTTP layer does not pass them from the client.
2. Persist both fields in `status.json`. `from_dict` accepts files that omit both. Do not persist a half pair.
3. `retry` and `mark_failed` must not clear the pin. Retry’s `replace` continues to reset status, error, and chunk counters only.
4. `process_job` loads the job first. When the pin is present, build the translation provider and `CacheIdentity` from it, and use the live base URL and timeout only as connection settings. When the pin is absent, keep today’s “current settings” behavior.
5. `GET /api/jobs/{id}` returns the pair for pinned jobs. Create’s `202` body stays `{ job_id, status }`.
6. Frontend `JobStatusResponse` includes the optional pair. The create form does not.
7. Factory helper used by the worker constructs exactly one translation adapter for the resolved provider. Tests assert the other heavy adapter class is not constructed.

### Explicitly unchanged

- Job states and legal transitions.
- Chunk ids, checkpoint skip, and “do not regenerate a successful piece.”
- Narration processor and TTS provider selection.
- `GET /api/capabilities` source (still the API process’s current translator).
- Compose default `TRANSLATION_PROVIDER=fake`. No new Compose service and no baked weights.
- Paid or public translation providers.

### Constitution Check (after design)

| Gate | Result |
| --- | --- |
| I–V | Still pass. The extra status fields are additive. Cache identity still includes provider and model, now taken from the job so one story cannot change keys mid-run. Vendor SDK stays inside the existing adapter. |

## Artifacts

- [research.md](./research.md)
- [data-model.md](./data-model.md)
- [contracts/lab-translator.md](./contracts/lab-translator.md)
- [contracts/job-status.md](./contracts/job-status.md)
- [quickstart.md](./quickstart.md)
