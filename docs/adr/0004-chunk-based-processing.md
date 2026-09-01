# ADR 0004: Chunk-based processing

- Status: Accepted
- Date: 2026-09-01

## Context

Translation and TTS APIs have size/time limits. Loading 100k+ characters of audio into RAM will crash a 16GB CPU box. Language-specific chunking (e.g. “Chinese sentence only”) would break other sources.

## Decision

Parse into chapters/paragraphs/sentences as available, then pack **stable `chunk_id`s** under character/token budgets that are **language-agnostic** (unicode-aware counts, not “English words”).

Each chunk is the unit of translation, narration, TTS, cache, retry, and checkpoint. Audio is written to per-chunk files and merged with FFmpeg from a concat list, not concatenated in Python memory.

## Consequences

Progress UI can show `12/35`. Partial failure is expressible. Chunk boundaries may need overlap/context later; do not add overlap until a provider quality issue is demonstrated.

## Alternatives

- One request per story — rejected (AC long-story).
- Tokenizers tied to a single script — rejected as a domain rule; adapters may apply extra limits.
