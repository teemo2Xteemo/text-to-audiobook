# ADR 0002: Provider abstraction

- Status: Accepted
- Date: 2026-09-01

## Context

MVP translation is NLLB; MVP TTS is Edge TTS. Both are likely to be replaced (LibreTranslate, Piper, ElevenLabs, Azure). Vendor SDKs differ in language codes, auth, rate limits, and audio containers.

## Decision

Domain defines `TranslationProvider` and `TTSProvider` ports. Concrete adapters live under `backend/app/providers/`. Application services receive ports via dependency injection. No `if provider == "..."` in domain, application, or HTTP routes.

Selection of which adapter is constructed is configuration (`TRANSLATION_PROVIDER`, `TTS_PROVIDER`).

## Consequences

Swapping Edge TTS for Piper should not change job state, chunking, or API. Adapter code may be vendor-specific and ugly; that is expected and contained.

## Alternatives

- Strategy enum + switches in one service — rejected; grows into a god object.
- Plugin marketplace in MVP — rejected as premature.
