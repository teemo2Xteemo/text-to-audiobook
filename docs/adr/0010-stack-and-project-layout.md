# ADR 0010: Stack and project layout

- Status: Accepted
- Date: 2026-09-01

## Context

The repository started as requirements-only. Agents need a single agreed stack so the first implementation does not fork into Next.js vs Vite vs Django.

## Decision

| Area | Choice |
| --- | --- |
| Frontend | React + TypeScript (Vite-style SPA is sufficient; Next.js not required for MVP) |
| Backend | Python + FastAPI |
| Queue | Redis + RQ worker |
| Translation MVP | NLLB adapter (CPU distilled) |
| TTS MVP | Edge TTS adapter |
| Audio | FFmpeg / FFprobe via argv |
| Run | Docker Compose |
| Layout | `docs/ai/target-structure.md` |

TypeScript **strict**. Python typed + Pydantic settings.

This ADR does not add those packages; it constrains the first scaffolding task.

## Consequences

Do not introduce a second API framework or a second frontend meta-framework without superseding this ADR.

## Alternatives

- Next.js fullstack — extra SSR complexity for a job-polling UI.
- Celery — see ADR 0003.
- Monolith UI in Jinja — rejected; requirements ask for React.
