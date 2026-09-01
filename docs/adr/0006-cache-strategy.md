# ADR 0006: Cache strategy

- Status: Accepted
- Date: 2026-09-01

## Context

Re-generating the same chunk wastes time and may hit provider rate limits. Caching by raw text alone mixes languages, voices, and models.

## Decision

Cache **translation** and **TTS** artifacts separately. Key material:

`operation + text + source_language + target_language + provider + model + voice + relevant settings`

Use a stable hash of a canonical serialization (sorted keys). Store blobs next to the job or in a content-addressed cache directory (gitignored). MVP may use the filesystem; Redis is for queue/status, not large MP3s.

A cache hit must still respect job ownership (do not serve another user’s file via a guessed hash if jobs become multi-user).

## Consequences

Changing speed or voice correctly misses cache. Agents must update the key helper when a setting becomes audible.

## Alternatives

- Text-only hash — rejected (wrong-language collisions).
- Always regenerate — rejected for long stories and resume.
