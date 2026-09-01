# Testing strategy

Correctness of chunking, state transitions, and cache keys matters more than hitting live NLLB/Edge TTS in CI.

## Layers

| Layer | How | Providers |
| --- | --- | --- |
| Domain | Pure unit tests | None |
| Application | Unit tests with fakes | Fake ports |
| API | FastAPI `TestClient` | Fake job service |
| Adapter | Optional `@pytest.mark.integration` | Real or recorded |
| E2E | Manual / compose profile | Real, not default CI |

## Fakes

Each port gets a fake that returns deterministic text/audio files on disk (tiny silent/fixture bytes), records calls, and can fail on a chosen `chunk_id` to test retry/resume.

Do not sleep on real network in unit tests.

## Required cases (when those modules exist)

- Job enum: legal transitions succeed; illegal transitions raise
- Chunker: long input yields multiple stable IDs; not language-specific
- Cache key: same text + different `target_language` or voice → miss
- Retry: failed chunk retried; successful neighbors left untouched
- Resume: checkpoint at N; restart continues at N
- Translation: `auto` vs explicit source; unsupported language
- TTS: voice rejected when incompatible with language
- API: `POST /api/jobs` 202 + `job_id`; validation errors use the error envelope
- Upload: oversize / bad MIME / `../` filename rejected

## What not to test in unit tests

Live Edge TTS, downloading NLLB weights, GPU, YouTube downloads, real billing APIs.

## Frontend

Test hooks/client mapping (job status → progress UI) with mocked `fetch`. Do not duplicate language matrices in frontend tests; use capability fixtures.
