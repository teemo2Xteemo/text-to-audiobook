# ADR 0007: Retry and resume

- Status: Accepted
- Date: 2026-09-01

## Context

Requirements Phase 1 lists a thin slice; AC-07/AC-08 require that one failed chunk not destroy the job and that workers can resume. Provider calls are flaky (Edge TTS, rate limits, OOM).

## Decision

MVP **includes** per-chunk retry and checkpoint resume, even if a richer “job history UI” waits for Phase 2.

- Retry policy is bounded and configurable (count + backoff). Only failed chunks are retried.
- After each successful chunk, persist a checkpoint (`chunk_id`, stage, artifact path).
- Worker start: load checkpoint, skip completed artifacts, continue.
- Idempotent writes: same job+chunk+config overwrites or skips, never forks duplicate finals.
- Job-level failure if a chunk exhausts retries; completed chunks remain on disk for later retry of the job.

## Addendum (2026-11-19): run budget

A book does not finish inside `RQ_JOB_TIMEOUT_SECONDS`. The worker passes a monotonic deadline (`JOB_RUN_BUDGET_SECONDS`, default timeout minus 120 seconds, always below the RQ timeout) into `PipelineOrchestrator.run`. Past the deadline the orchestrator stops taking new chunks, waits for in-flight work, and returns the same non-terminal status with `continue_requested`. RQ `on_success` (`handle_rq_success`) re-enqueues that job at the back of the queue and increments `run_count`. A `JobTimeoutException` after the checkpoint count grew does the same instead of `mark_failed`. No checkpoint growth for `MAX_STALLED_RUNS` consecutive runs, or `run_count >= MAX_JOB_RUNS`, fails the job with `TIMEOUT`. `recovery_attempts` resets when this run records a new checkpoint, including inside one long stage. User retry still goes `FAILED → QUEUED` and clears run, stall, and marker fields.

Checkpoints are an append-only `checkpoint.jsonl` (last-write-wins, compacted when the log has duplicates or a legacy `checkpoint.json` is still present). `checkpoint.json` remains readable so a job started before this change can resume.

## Consequences

Need durable chunk status (files + metadata). Testing must include “fail chunk 3, succeed rest.” Long jobs must also be tested for yield, continuation, stall, and legacy checkpoint migration.

## Alternatives

- Restart entire job on any failure — rejected by AC-07.
- Defer resume to Phase 2 — rejected; it is cheaper to checkpoint while designing storage than to bolt on later.
