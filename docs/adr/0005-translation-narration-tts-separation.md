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

## Addendum: Narrate Mode & Pass-Through Pipeline (2026-10-28)

### Context

Users often wish to generate audiobooks from source text directly without translation (e.g. stories already in the intended listening language, or self-narrated material). Invoking translation models in this scenario incurs unnecessary latency, compute costs, and risks introducing hallucinations or altering phrasing. Furthermore, users requesting `translate` mode with identical source and target languages (`source == target`) should not trigger redundant model calls.

### Decision

1. **Job Modes (`JobMode`):**
   - Introduce explicit job modes: `"translate"` (default) and `"narrate"`.
   - **Narrate Mode:** Synthesizes speech directly from text/TXT in the source language without translation or LLM calls.
   - In narrate mode, `target_language` defaults to the resolved `source_language`, and translation configuration (`translation_profile_id`) is rejected.

2. **Same-Language Translate Bypass:**
   - In translate mode, if `source_language == target_language` (for both plain text and SRT inputs), translation calls are bypassed automatically.

3. **Pass-Through Chunks & Checkpoint Reuse (`_pass_through_chunks`):**
   - Both Narrate mode and same-language Translate bypass reuse the orchestrator's pass-through chunk mechanism (`_pass_through_chunks`).
   - The original chunks are copied to `chunks/<id>.translated.txt` and recorded under the `STAGE_TRANSLATED` checkpoint.
   - This ensures full compatibility with the existing three-stage architecture: downstream narration formatting (`_narrate_chunks`), audio generation (`_generate_audio`), audio merging (`_finish_audio`), checkpoint resumption, and cache lookups execute identically without specialized branching.

4. **Language-Family Voice Fallback & Dialect Separation:**
   - Voice resolution (`resolve_voice`) falls back across regional variants within the same primary language family (e.g., falling back to `zh-CN` or `zh-TW` for generic `zh`) based on configured preferences (`FAMILY_PREFERENCES`).
   - **Bidirectional Cantonese/Mandarin Separation:** Cross-fallback between Cantonese (`zh-HK`, `zh-MO`, `zh-yue`, `yue`) and Mandarin (`zh`, `zh-CN`, `zh-TW`, `cmn`) is strictly forbidden in both directions.
   - **Dialect Voice Exclusion:** Regional dialect voices (e.g., Liaoning, Shaanxi, Shandong, Sichuan) are excluded from generic Mandarin fallback unless explicitly selected by the user.

### Consequences

- Zero model cost and near-instant initial pipeline progression for direct narration.
- Checkpoint persistence and resume behavior remain completely unified across both modes.
- Narration text processing continues to clean symbols, format numbers, and insert natural punctuation pauses before TTS synthesis.


## Addendum: Subtitle Timing, Chapter Pauses & Speech Normalization (2026-10-31)

### Context

Issue #28 Phases 2 and 4 extend narrate mode for subtitles and long books, and add an optional
LLM pass that turns numbers, dates and abbreviations into spoken words.

### Decision

1. **Continuous subtitles by default:** narrate + `.srt`/`.vtt` joins cues into paragraphs and
   breaks only after a > 2 s gap that follows a finished sentence (`strip_srt`), so cue boundaries
   never cause mid-sentence pauses.
2. **Per-job `srt_timing`:** `aligned` routes narrate + subtitles through the existing cue timeline
   (`_run_srt` with pass-through). Translate mode keeps the timeline and rejects the field.
3. **Chapter pauses:** chunks carry their chapter ordinal; the narrate merge inserts
   `NARRATE_CHAPTER_PAUSE_MS` of silence at chapter boundaries. Per-chapter files stay with #29.
4. **Speech normalization is a separate, opt-in port:** `TextNormalizer` runs inside the narration
   stage before `NarrationProcessor`, only when the job sets `normalize_text`. It never translates.
   Results are cached under their own `normalization` cache operation keyed by normalizer
   provider/model, independent of the translation and TTS keys.

### Consequences

- Narrate stays model-free unless a job opts into normalization; the UI labels that cost.
- The worker builds the normalizer only for opted-in jobs, so a missing LLM key cannot fail other jobs.
- Only OpenAI-compatible chat providers (Groq, OpenRouter) implement `TextNormalizer` today.
