# ADR 0003: Async job architecture

- Status: Accepted
- Date: 2026-09-01

## Context

A long story can take minutes. A blocking `POST /generate` will time out, pin API workers, and hide progress.

## Decision

- `POST /api/jobs` validates input, stores the source, enqueues work, returns `{ job_id, status }` (HTTP 202).
- Redis is the queue (and may hold job progress for MVP).
- A worker process runs the pipeline and updates status.
- `GET /api/jobs/{job_id}` returns stage, chunk progress, errors, and result locator.
- MVP job metadata: Redis + filesystem under `storage/jobs/{job_id}/`. No Postgres until job history/auth needs it (Phase 2+).
- Worker implementation: **RQ** for MVP (Redis-native, small surface). Revisit Celery if routing/canvas becomes necessary.
- A long job is still one RQ task id (`process-{job_id}`) at a time. When the cooperative run budget is spent, that task **succeeds** and `on_success` enqueues the same id again. The job status stays the pipeline stage (not `FAILED`, not reset to `QUEUED`) until the book completes, stalls, or hits `MAX_JOB_RUNS`. Phase 2 per-chapter fan-out is out of scope until chapter manifests exist.

## Consequences

API instances stay request-scoped. Horizontal scale is “more workers.” The UI must poll (or later websocket) rather than wait on one HTTP call.

## Alternatives

- In-process FastAPI `BackgroundTasks` — rejected for crash isolation and multi-worker deploy.
- Celery first — extra concepts without MVP benefit.
- Postgres from day one — extra moving part before there is a user table.
