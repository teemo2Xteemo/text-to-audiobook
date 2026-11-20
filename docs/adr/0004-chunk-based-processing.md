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

## Addendum: per-chapter audio (issue #29)

A detected span (preface, volume, or chapter) is also an output unit. The worker writes `jobs/<id>/chapters.json` once, next to the saved chunk plan, before any chunk work. Counts stay off `Job`: `GET /api/jobs/{id}` reads the manifest. Each span's file is `chapters/{ordinal:04d}.{ext}`, where `ordinal` is the unique span index from chapter detection, not the printed chapter number.

Assembly runs as soon as every chunk in that span is normalized, including when a run budget yields before the book merge. A finished `chapter-NNNN` checkpoint is not rebuilt. The merged `output.{ext}` is still produced for `GET /audio` after the job completes. Jobs that were already checkpointed without a manifest keep the previous packer and the single-file download.

Timed SRT stays one timeline and does not get a manifest. Unheaded text is one span: the UI keeps the single download button and hides the chapter list when `chapters_total` is 0 or 1.

ZIP downloads stream ready files with ZIP64 and store compression (no second encode, no full-book buffer). M4B export, per-chapter retry, and continuing past a failed chapter are not part of this addendum.
