---

description: "Task list for optional local literary translation"
---

# Tasks: Optional Local Literary Translation

**Input**: Design documents from `/specs/001-optional-local-translation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. The constitution and `quickstart.md` require automated coverage for this behavior change. Write each story’s tests so they fail before that story’s implementation. Do not add a live-model CI test.

**Organization**: Tasks are grouped by user story so each story can be implemented and tested on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- Backend: `backend/app/`, `backend/tests/`
- Frontend type only: `frontend/src/api/types.ts`
- The Ollama adapter at `backend/app/providers/translation/ollama.py` already exists. Do not add a provider module or a dependency.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Stay on the current tree and the existing literary adapter

- [x] T001 Confirm `backend/app/providers/translation/ollama.py` and `backend/app/config/settings.py` stay the literary path (`OLLAMA_BASE_URL`, `OLLAMA_TRANSLATION_MODEL`, `OLLAMA_HTTP_TIMEOUT_SECONDS`) and add no package or new provider file

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Job pin fields that every story reads and writes

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Write failing round-trip tests in `backend/tests/domain/test_job_record.py` for `translation_provider` and `translation_model`: new records store both fields together; `from_dict` accepts records that omit both; a half pair (only one of the two keys) is treated as a missing pin rather than inventing the other half; a present provider id is one of `fake`, `nllb`, or `ollama`; a present model id is non-empty
- [x] T003 Implement those `Job` fields and `to_dict` / `from_dict` rules in `backend/app/domain/jobs.py` so T002 passes. Do not add a job status or change `can_transition` / `assert_legal_transition`

**Checkpoint**: Older `status.json` files still load. New in-memory jobs can carry both pin fields.

---

## Phase 3: User Story 1 - Narrate a story the default translator mangles (Priority: P1) 🎯 MVP

**Goal**: A lab choice of the optional literary translator is copied onto every new story, shown on status, and used by the worker and translation cache even if the worker environment later names another translator.

**Independent Test**: With the lab provider set to `ollama` and the model set to `translategemma:4b`, create two stories without a translator field on the request. Both status records show that pair. Processing the first story builds only the Ollama adapter for that pair.

### Tests for User Story 1

- [x] T004 [P] [US1] Add a failing test in `backend/tests/application/test_create_job.py`: when the lab provider is `ollama` and the model is `translategemma:4b`, `JobService.create` stores both pin fields from the service settings. The pin is copied from the lab setting inside the application service. It is not taken from the create request. A second create under the same lab settings stores the same pair. New jobs store both pin fields together
- [x] T005 [P] [US1] Add a failing test in `backend/tests/api/test_jobs.py`: `POST /api/jobs` still returns only `job_id` and `status`. `GET /api/jobs/{job_id}` returns `translation_provider` and `translation_model` for a job created under the lab choice. The create response does not gain these fields
- [x] T006 [P] [US1] Add a failing test in `backend/tests/workers/test_process_job.py`: a job whose pin is `ollama` and `translategemma:4b` is translated with that pair when the worker environment says `nllb`. Building that adapter must not construct `NllbTranslationProvider`

### Implementation for User Story 1

- [x] T007 [US1] Stamp the lab pin in `backend/app/application/jobs.py` `JobService.create`, passing the provider id and model id from `backend/app/config/factory.py` `build_job_service`. Do not add those fields to `CreateJobCommand`. Provider id is `fake`, `nllb`, or `ollama`. Model id is `fake`, `NLLB_MODEL_ID`, or `OLLAMA_TRANSLATION_MODEL` for the matching provider, and is non-empty
- [x] T008 [P] [US1] Add `translation_provider` and `translation_model` to `JobStatusResponse` in `backend/app/api/schemas.py`. Leave `JobCreatedResponse` as `job_id` and `status` only. Omit the pair when the job has no pin
- [x] T009 [US1] When both pin fields are present, build that job’s `TranslationProvider` and `CacheIdentity` from them in `backend/app/config/factory.py` and use that pair from `backend/app/workers/runner.py`. Keep `OLLAMA_BASE_URL` (an `http` or `https` URL) and `OLLAMA_HTTP_TIMEOUT_SECONDS` as live connection settings. Construct exactly one translation adapter for the resolved provider
- [x] T010 [P] [US1] Add optional `translation_provider` and `translation_model` to `JobStatusResponse` in `frontend/src/api/types.ts`. Do not add a translator control to the create form

**Checkpoint**: User Story 1 is testable alone. New stories follow the literary lab choice and keep that choice while they run.

---

## Phase 4: User Story 2 - Keep the packaged default (Priority: P2)

**Goal**: A lab left on the packaged offline stand-in (`fake`) still creates stories without a paid account, without the literary model, and without a translator field on submission. Selecting the free on-device translator (`nllb`) also stays free of the literary model.

**Independent Test**: Create a short story with `TRANSLATION_PROVIDER=fake` and no literary model installed. The stored pin is `fake` / `fake`. The NLLB and Ollama adapter classes are not constructed. The create body has no translator field. Building an `nllb` pin does not construct the Ollama adapter.

### Tests for User Story 2

- [x] T011 [P] [US2] Add a failing test in `backend/tests/application/test_create_job.py`: with lab provider `fake`, create stores `translation_provider` `fake` and `translation_model` `fake`, and `CreateJobCommand` has no translator or model field
- [x] T012 [P] [US2] Add a failing test in `backend/tests/config/test_factory.py`: the packaged default `translation_provider` is `fake`; building the `fake` pin constructs neither `NllbTranslationProvider` nor `OllamaTranslationProvider`; building an `nllb` pin does not construct `OllamaTranslationProvider`; building an `ollama` pin does not construct `NllbTranslationProvider`

### Implementation for User Story 2

- [x] T013 [US2] Keep `CreateJobJsonBody` in `backend/app/api/schemas.py` and `CreateJobCommand` in `backend/app/application/jobs.py` without translator or model fields. Keep `translation_provider` default `fake` in `backend/app/config/settings.py`. Do not add an Ollama service or model weights to `docker-compose.yml`

**Checkpoint**: User Stories 1 and 2 both work. The packaged default does not require the literary model.

---

## Phase 5: User Story 3 - Change models and recover from local failures (Priority: P3)

**Goal**: An unfinished story keeps the translator and model it started with, including retry. A story created after the lab choice changes uses the new pair and does not reuse the earlier story’s translation.

**Independent Test**: Translate one piece, change the lab choice, and continue the first story on its original pin. Create a second story and see the new pin. Retry a failed job and the original pin is still on disk. A status file with no pin still follows current settings.

### Tests for User Story 3

- [x] T014 [P] [US3] Add a failing test in `backend/tests/application/test_create_job.py`: `retry` and `mark_failed` keep both pin fields; `retry` still resets `status`, `error_type`, `message`, `chunk_current`, and `chunk_total` only. After the lab provider or model changes, a newly created job stores the new pair and the earlier job’s pair is unchanged
- [x] T015 [P] [US3] Add a failing test in `backend/tests/workers/test_process_job.py`: a pinned unfinished job still uses its stored provider and model when the worker environment has changed. A status file that omits both pin fields uses current settings. A half pair is treated as a missing pin rather than inventing the other half
- [x] T016 [P] [US3] Add a failing test in `backend/tests/config/test_factory.py`: translation `CacheIdentity` for a pinned job uses that job’s provider and model, not the live settings, so a different model does not satisfy the same translation lookup

### Implementation for User Story 3

- [x] T017 [P] [US3] Preserve `translation_provider` and `translation_model` in `retry` and `mark_failed` in `backend/app/application/jobs.py`. Do not drop them in the `replace` calls. Retry, failure marking, and stage updates preserve both fields
- [x] T018 [US3] In `backend/app/workers/runner.py`, use the job pin when both fields are present and ignore a later lab provider or model change. When both are absent, or only one key is present, use current settings as today’s behavior for older records

**Checkpoint**: All three stories work. Changing the lab choice affects only stories created afterward.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Logs stay free of story text, the existing adapter and pipeline tests still pass, and the manual sample stays optional

- [x] T019 [P] Confirm job and adapter logs in `backend/app/application/jobs.py` and `backend/app/providers/translation/ollama.py` can record provider and model ids and still omit story text and credentials. Confirm `.env.example` still documents `TRANSLATION_PROVIDER=fake` and `OLLAMA_TRANSLATION_MODEL=translategemma:4b` used only when the provider is `ollama`
- [x] T020 Run the automated checks in `specs/001-optional-local-translation/quickstart.md` (`pytest -m "not integration"` including `backend/tests/domain/test_job_record.py`, `backend/tests/application/test_create_job.py`, `backend/tests/application/test_pipeline.py`, `backend/tests/api/test_jobs.py`, `backend/tests/workers/test_process_job.py`, `backend/tests/config/test_factory.py`, `backend/tests/providers/test_ollama_translation.py`) and Ruff format/lint on the touched Python files
- [ ] T021 (optional, not a CI gate) Follow “Manual literary sample” in `specs/001-optional-local-translation/quickstart.md` and record whether a reviewer can narrate the documented sample’s names, medical term, and idiom without rewriting them. Do not add an automated similarity score or a live-translator test to the default suite

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup. Blocks all user stories
- **User Stories (Phase 3–5)**: Depend on Foundational. Story 2 and Story 3 also use the create/worker path from Story 1
- **Polish (Phase 6)**: Depends on the stories you intend to ship

### User Story Dependencies

- **User Story 1 (P1)**: Starts after Foundational. No dependency on Story 2 or 3
- **User Story 2 (P2)**: Starts after Story 1 stamps whatever the lab settings are. Testable with `fake` without a literary model
- **User Story 3 (P3)**: Starts after Story 1. Extends retry and the worker; does not require Story 2

### Within Each User Story

- Tests fail before implementation
- Domain fields exist before services
- Create stamps the pin before status, worker, and retry tasks rely on it
- Story 1’s factory wiring (T007) finishes before T009, because both edit `backend/app/config/factory.py`

### Parallel Opportunities

- T004, T005, T006, and T010 touch different files
- T008 can run alongside T007 (schemas vs. application service)
- T011 and T012 touch different files
- T014, T015, and T016 touch different files
- T017 can run alongside the worker follow-up once T014 exists; T018 waits for T009 because both edit `backend/app/workers/runner.py`
- T019 can run alongside the last implementation task
- T021 is optional and does not block T020

---

## Parallel Example: User Story 1

```bash
# Tests and the frontend type together, before implementation:
Task: "T004 failing create test in backend/tests/application/test_create_job.py"
Task: "T005 failing API test in backend/tests/api/test_jobs.py"
Task: "T006 failing worker test in backend/tests/workers/test_process_job.py"
Task: "T010 optional status fields in frontend/src/api/types.ts"

# After those tests exist:
Task: "T007 stamp the pin in backend/app/application/jobs.py"
Task: "T008 status schema in backend/app/api/schemas.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1 and Phase 2
2. Complete Phase 3 (User Story 1)
3. Stop and run the User Story 1 tests (T004–T006 plus T002)
4. Demo: two new stories show the literary pin and the worker stays on that pin

### Incremental Delivery

1. Setup + Foundational → job records can store a pin
2. User Story 1 → literary lab choice sticks to new and in-flight stories
3. User Story 2 → packaged offline stand-in (`fake`) still needs no literary model, and `nllb` stays the free on-device translator when selected
4. User Story 3 → retry and a later lab change cannot move an unfinished story

### Parallel Team Strategy

After Phase 2, one person can finish User Story 1 (the create/worker path). Story 2 and Story 3 should wait for T007 and T009, because they extend those same files rather than a separate feature slice.

---

## Notes

- [P] tasks use different files and do not depend on an unfinished edit to the same file
- The adapter’s truncation guard, commentary stripping, and typed errors stay in `backend/app/providers/translation/ollama.py`; do not reimplement them here
- Empty source text stays rejected by today’s create path. Do not add a new empty-piece error
- T021 is the optional manual sample check. It stays out of CI, as in `quickstart.md`
