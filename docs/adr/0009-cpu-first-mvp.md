# ADR 0009: CPU-first MVP constraint

- Status: Accepted
- Date: 2026-09-01

## Context

Target environment: CPU, ~16GB RAM, no GPU required, `docker compose up`. NLLB-200 3.3B and XTTS-class models are a poor default on that box.

## Decision

- MVP translation: NLLB **distilled** (e.g. 600M-class) or equivalent CPU-runnable model, configured by env (model id is not a domain constant).
- MVP TTS: Edge TTS (network) or Piper later for fully offline.
- No CUDA images in default Compose.
- Concurrency stays low and configurable so RAM does not explode (one heavy model + ffmpeg).
- GPU/XTTS/Chatterbox are Phase 3+ **adapters**, not Compose defaults.

## Consequences

Translation quality may trail GPU/LLM options. That is an accepted MVP trade. Do not silently add `runtime: nvidia` to make NLLB “work.”

## Alternatives

- Require GPU for MVP — rejected by AC-09.
- Cloud translation APIs only — conflicts with free-first unless configured as an optional adapter.
