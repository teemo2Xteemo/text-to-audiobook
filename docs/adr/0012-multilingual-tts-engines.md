# ADR 0012: Multilingual TTS engines (Edge default; Fish and VieNeu optional)

- Status: Proposed — pending owner listening pilot
- Date: 2026-11-01
- Issue: [#28](https://github.com/teemo2Xteemo/text-to-audiobook/issues/28)

## Context

Narrated stories are often mixed-language: Vietnamese prose with Sino-Vietnamese character names, English terms (code-switching), numbers and dates, `[System]` / `【…】` brackets, and dialogue. Edge TTS (ADR 0008) is the free default but may mishandle names, English fragments, and numbers. Other engines (Fish Audio, VieNeu-TTS) may do better on some of these, at different cost, latency, and rate-limit profiles.

Constraints: ADR 0001 (language-agnostic), ADR 0002 (provider ports), ADR 0004 / 0007 (chunks, retry, resume), ADR 0006 (cache identity includes provider + model), ADR 0009 (CPU-first, free default). Switching engine must never be silent: a job that pins an engine either uses it or fails with a typed error; it never falls back to another engine.

## Decision (implemented on the issue #28 Phase 3 branch)

1. **Adapters behind `TTSProvider`:** `edge`, `fish` (`backend/app/providers/tts/fish.py`), `vieneu` (`backend/app/providers/tts/vieneu.py`). Domain code sees only the port. Voice ids are namespaced (`fish:<reference_id>`, `vieneu:<name>`).
2. **Per-job `tts_engine` pin:** a job may pin an engine from `TTS_ENGINES_ENABLED` (the default engine is always allowed). Create runs a preflight (`check_available()`) only for pinned engines; provider configuration problems return HTTP 400. The `tts_engines` capability lists what is enabled. Preview is engine-aware.
3. **Fish Audio:** HTTP `POST {FISH_AUDIO_BASE_URL}/v1/tts` with the `model` header set from `FISH_AUDIO_MODEL` (default `s2.1-pro-free`), Bearer `FISH_AUDIO_API_KEY` (env only), mp3 output, voices from `FISH_AUDIO_VOICES` (`lang=reference_id[:label],...`). The model feeds the TTS cache key.
4. **Rate-limit retry policy:** `PROVIDER_RATE_LIMIT` (HTTP 429) uses its own policy: 6 attempts (`RETRY_RATE_LIMIT_MAX_ATTEMPTS`), 5 s base backoff doubling per attempt (`RETRY_RATE_LIMIT_BACKOFF_SECONDS`), honouring a provider `retry_after` hint, and each wait capped at `max_wait_seconds` = 120 s. Other retryable errors keep the general policy.
5. **VieNeu-TTS:** optional Compose profile `vieneu` (`docker compose --profile vieneu up`), built from upstream at a pinned commit (`04044e1b8f822f946bafb2c88b716459f4164152`; bumping is a manual reviewed change). CPU, `VIENEU_PRECISION=fp32` by default (`int8` only on a VNNI-capable CPU; the i5-10300H lab host is not one). Adapter speaks an OpenAI-style speech API (`/v1/audio/speech`, `/v1/voices`, `/health`), `vi-VN` only.
6. **Edge stays the default** engine. This ADR changes no default.

## Pilot protocol (owner-run)

**Fixture:** `backend/tests/fixtures/narrate-mixed-vi-en.txt` (one chapter: Sino-Vietnamese names, English terms, numbers/dates, `[System]` and `【…】` brackets, dialogue).

**Setup:** `TTS_ENGINES_ENABLED=edge,vieneu,fish`; one narrate job per engine/voice, same fixture, same speed.

| Engine | Voice(s) |
| --- | --- |
| Edge | `vi-VN-HoaiMyNeural`, `vi-VN-NamMinhNeural` |
| VieNeu (fp32) | TBD (owner): voices from `/v1/voices` |
| Fish `s2.1-pro-free` | 1-2 reference voices (TBD owner) |

**Blind A/B:** listeners do not know the engine. Score 1-5 per criterion: names, English code-switching, numbers and dates, prosody, long-form consistency, artifacts (clicks, truncation, repeated words).

**Metrics to record per run:** wall time; RTF on the real host (synthesis time / audio duration); 429 count; other error count; estimated cost per 100 h of audio.

### Results: listening scores (1-5)

| Engine / voice | Names | English | Numbers | Prosody | Long-form | Artifacts |
| --- | --- | --- | --- | --- | --- | --- |
| Edge HoaiMy | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) |
| Edge NamMinh | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) |
| VieNeu fp32 | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) |
| Fish s2.1-pro-free | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) |

### Results: operations

| Engine / voice | Wall time | RTF | 429 count | Other errors | Est. cost / 100 h |
| --- | --- | --- | --- | --- | --- |
| Edge | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) |
| VieNeu fp32 | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) |
| Fish s2.1-pro-free | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) | TBD (owner) |

### Per-language default decision

TBD (owner). Edge remains the default until this section is filled and the ADR is Accepted. Any default change gets its own one-line commit after the ADR is merged.

## Open questions

- **VieNeu upstream API contract:** the voices JSON shape is parsed leniently (`voices`/`data` list or bare list; items as strings or objects with `id`/`name`/`voice`); confirm against the pinned SHA.
- **VieNeu `speed`:** the adapter sets `honours_speed = False` and the orchestrator applies a tempo fallback. If upstream honours `speed`, set it to `True` to avoid applying tempo twice.
- **VieNeu precision:** confirm the container actually reads `VIENEU_PRECISION` (Compose passes it to the sidecar and the worker; the worker uses it for the cache key).
- Fish free-tier rate limits and cost per 100 h are unmeasured.

## Consequences

- Engines are opt-in per job; the offline default stack is unchanged (no vieneu container, no Fish key).
- Engine, model, and precision are part of the cache identity, so switching never reuses another engine's audio.
- Fish sends story text to a third-party API; operators opt in explicitly.

## References

- Issue [#28](https://github.com/teemo2Xteemo/text-to-audiobook/issues/28)
- ADR 0001, 0002, 0004, 0006, 0007, 0008, 0009
- `backend/app/providers/tts/`, `docker-compose.yml`, `.env.example`
