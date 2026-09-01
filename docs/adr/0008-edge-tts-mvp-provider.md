# ADR 0008: Edge TTS as initial TTS provider

- Status: Accepted
- Date: 2026-09-01

## Context

Edge TTS is free for personal/dev use, multilingual, and needs no API key. It is **not** a committed commercial API and must not become the domain.

## Decision

Implement `EdgeTTSProvider` as the first `TTSProvider` adapter. Voices are whatever the adapter lists for `target_language`; no universal `vi-VN-*` default in domain code.

A config default voice **per language** (or “first listed voice for this language”) is allowed.

Before paying for ElevenLabs/XTTS, compare quality to the operator’s reference narration using analysis — do not claim a vendor from a YouTube URL without evidence (requirements §33).

## Consequences

Audio from Edge TTS will differ in container/bitrate from later providers; FFmpeg normalization is mandatory (ADR 0004). ToS/rate-limit risk is accepted for MVP and is a reason the port exists.

## Alternatives

- Piper-only MVP — better offline story, often weaker multilingual coverage for the first demo.
- ElevenLabs-first — conflicts with $0/CPU-first goals.
