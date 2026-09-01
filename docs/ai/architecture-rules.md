# Architecture rules (why)

Cursor rules say *what* to do. This page says *why*, and how to resolve conflicts in the requirements doc.

## Language-agnostic core

The UI mock and several acceptance examples use Chinese input and Vietnamese audio. The *product* is still:

```text
source_language → target_language → voice compatible with target_language
```

Hard-coding a pair makes every other pair a rewrite. Provider language coverage will change; domain language identity must not.

**Resolution of §2 vs §3/§6/§40:** §2’s “Chinese story / Vietnamese TTS” is the primary demo path. Domain code uses `source_language` / `target_language`. UI defaults (if any) belong in **config**, e.g. `DEFAULT_TARGET_LANGUAGE`, not in services.

## Provider ports

Edge TTS and NLLB are MVP *adapters*. Vendor APIs, rate limits, and language code tables rot quickly. Ports keep job orchestration stable when swapping Piper, LibreTranslate, or ElevenLabs.

`if provider == "edge"` in application/domain is the failure mode these rules exist to prevent.

## Pipeline stages

Translation quality ≠ narration rhythm ≠ TTS rendering ≠ audio container. Mixing them produces an untestable god service and makes “retry TTS only” impossible.

Narration is required for AC-12 (storytelling rhythm). It must be a separate, testable stage.

## Jobs, chunks, cache, resume

Stories at 10k–100k+ characters cannot run inside one HTTP request or one model call. Chunk-level retry/resume is correctness, not optimization: a 90% complete job must not restart from chunk 1.

Cache keys include languages, provider, model, voice, and settings so `zh→vi` never returns a `zh→en` clip.

## CPU-first MVP

The target machine is CPU / ~16GB RAM / no GPU. That constrains model choice (NLLB distilled, Edge TTS) but **not** the port shapes. See ADR 0008 and 0009.

## Requirements conflicts (resolved)

| Tension | Resolution |
| --- | --- |
| Functional goals list Chinese→Vietnamese only | Treat as example; architecture is language-agnostic (ADR 0001) |
| Phase 1 omits cache/resume; AC-07/AC-08 require retry/resume | MVP **includes** chunk retry + resume; simple filesystem cache is allowed in Phase 1 (ADR 0007) |
| UI mock shows “Vietnamese” / “Vietnamese Male” | Labels from capabilities + config defaults, not exclusive support |
| `DEFAULT_SOURCE_LANGUAGE=zh` in requirements §27 | Allowed **as env/config**, never as domain constants |
| Voice quality text talks about Vietnamese pronunciation | Quality bar applies to **whichever target language** is selected |

Do not “fix” these by encoding zh/vi in Python/TS modules.
