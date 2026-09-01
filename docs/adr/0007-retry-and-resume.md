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

## Consequences

Need durable chunk status (files + metadata). Testing must include “fail chunk 3, succeed rest.”

## Alternatives

- Restart entire job on any failure — rejected by AC-07.
- Defer resume to Phase 2 — rejected; it is cheaper to checkpoint while designing storage than to bolt on later.
