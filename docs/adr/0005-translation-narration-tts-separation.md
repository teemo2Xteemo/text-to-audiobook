# ADR 0005: Translation, narration, and TTS separation

- Status: Accepted
- Date: 2026-09-01

## Context

Literal translation read by TTS sounds robotic. AC-12 requires storytelling rhythm. Folding punctuation/pause logic into the translator or the TTS adapter makes both untestable and vendor-locked.

## Decision

Three stages, three modules:

1. **Translation** — meaning in `target_language` (natural, not word-for-word).
2. **Narration processing** — sentence/paragraph boundaries, pauses, dialogue, numbers, abbreviations, symbol cleanup, without changing meaning.
3. **TTS** — render narration text with a voice valid for `target_language`.

Translated text is not passed straight to TTS.

## Consequences

Narration can be unit-tested with plain strings. Providers can change without rewriting pause logic. Over-aggressive rewriting is a product risk; keep transformations conservative and documented.

## Alternatives

- Prompt an LLM to “write a script and speak it” in one call — rejected for MVP cost/CPU and weak retry granularity.
- TTS SSML-only, skip narration — insufficient for punctuation/number normalization across providers.
