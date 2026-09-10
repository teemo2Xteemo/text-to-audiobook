# ADR 0011: Ollama + TranslateGemma as first optional LLM translation adapter

- Status: Accepted
- Date: 2026-09-10
- Issue: [#12](https://github.com/teemo2Xteemo/text-to-audiobook/issues/12)

## Context

NLLB-200 distilled 600M (CPU MVP per ADR 0009 / 0010) produces unusable *literary* translation on the demo path (example: Chinese story → Vietnamese narration): truncation of later spans and mistranslation of names, medical terms, and idioms. Operator quality bar matches ChatGPT-style literary translation, not NMT-only output.

Issue #12 originally proposed **OpenAI** as the first optional LLM translation vendor. The project is still in a **lab** phase and does not want paid OpenAI tokens yet. Free/local options were surveyed on the issue thread; the strongest fit is **Ollama + TranslateGemma** (open weights, zh + vi supported, $0 after model download, data stays on the machine).

Constraints that still apply:

- ADR 0002: `TranslationProvider` port; adapters under `backend/app/providers/`; selection via `TRANSLATION_PROVIDER`.
- ADR 0005: do not merge translation + narration.
- ADR 0006: cache identity includes provider + model (+ languages, etc.).
- ADR 0009: Compose/MVP default stays CPU-first and free; cloud/LLM translators are **optional adapters**, not silent Compose defaults. Do not co-load a second heavy model beside NLLB on ~16 GB RAM.
- ADR 0001: language-agnostic; zh→vi is a demo fixture, not architecture.
- Secrets only via environment; never commit keys or bake weights into the API image.

## Decision

1. **First optional LLM translation adapter** is **Ollama** calling a **TranslateGemma** (or compatible) model over Ollama’s HTTP API — *not* OpenAI-as-first.
2. **Compose / offline MVP default remains NLLB** (`TRANSLATION_PROVIDER=nllb` with `NLLB_MODEL_ID` as today). Selecting the LLM adapter is an explicit env change.
3. **OpenAI** (and Gemini, etc.) remain valid *later* optional adapters on the same port; they are **out of scope for the first implementation** of this ADR.
4. **This ADR is Accepted.** Adapter code may follow in a later PR against this decision. Do **not** supersede ADR 0009 / 0010 unless a future decision makes a paid/cloud translator the Compose default.
5. Model id, base URL, and timeouts are **configuration**, not domain constants.

### Configuration (names only — no secrets in git)

| Env | Role | Suggested default when provider is ollama |
| --- | --- | --- |
| `TRANSLATION_PROVIDER` | Adapter selector | `nllb` (Compose default). Optional: `ollama` |
| `OLLAMA_BASE_URL` | Ollama HTTP base | `http://127.0.0.1:11434` (or Compose service DNS) |
| `OLLAMA_TRANSLATION_MODEL` | Model tag | `translategemma:4b` for ~16 GB lab smoke; `translategemma:12b` when quality matters and NLLB is not co-loaded |
| Existing `NLLB_MODEL_ID` | Unchanged | Still used only when provider is `nllb` |
| `RQ_JOB_TIMEOUT_SECONDS` | Worker soft timeout | Keep operator-tunable; LLM first-token / long chapters may need higher values than NLLB |

Document names in `.env.example` only. No API keys for Ollama local. If a future remote Ollama needs auth headers, add an optional env later — do not invent secrets in this ADR.

### Adapter shape

- Path: `backend/app/providers/translation/ollama.py` (name may vary; keep under `providers/translation/`).
- Implements domain `TranslationProvider`: `translate(text, source_language, target_language)` and `supported_languages()`.
- Map **domain BCP-47** (`zh-CN`, `vi-VN`, …) to whatever the model/chat template expects **inside the adapter** (ISO 639-1, `zh-Hans`, etc.). Unsupported → `UNSUPPORTED_LANGUAGE`.
- Transport: HTTP to Ollama (chat or generate API). Prefer stdlib/`httpx`-style client already used by similar network adapters (e.g. Edge). **No** Ollama/Gemma SDK in domain, application, or routes.
- Register only in `config/` / DI when `TRANSLATION_PROVIDER=ollama`.
- Failures: typed `TRANSLATION_FAILED`, `TIMEOUT`, `PROVIDER_RATE_LIMIT` where applicable — not bare 500s.
- Prompting: translation-only output (no commentary). Use TranslateGemma’s intended language-code / chat-template conventions where documented; keep prompts free of hard-coded demo pairs as *architecture* (zh→vi fixtures stay in tests/samples only).
- **Chunking:** respect model context for translation quality (TranslateGemma effective translation context is on the order of ~2K tokens). Pipeline already chunks; adapter or stage must not send unbounded chapter blobs in one call. Truncation was a NLLB failure mode — do not reintroduce it by oversizing prompts.
- **Cache:** include provider id + model id in cache keys (ADR 0006). Changing `OLLAMA_TRANSLATION_MODEL` must miss old NLLB/OpenAI entries.

### Process / RAM policy (lab)

- Only **one** heavy translation backend should be “hot” for a worker profile: when `TRANSLATION_PROVIDER=ollama`, do **not** also load NLLB weights in that worker.
- Recommended lab models: `translategemma:4b` (comfortable on ~16 GB) or `translategemma:12b` (tighter; better quality). `27b` is not a lab default.
- Ollama may run (a) on the host, (b) as an optional Compose service — document both; default Compose stack for MVP **must still boot offline with fakes/NLLB** without requiring Ollama pulled.

### Documentation updates required with the implementation PR

- `.env.example` — new names only.
- `docs/ai/provider-development.md` — list Ollama/TranslateGemma as the first optional LLM path; keep LibreTranslate / OpenAI / Gemini as later.
- README troubleshooting — Ollama not reachable, model not pulled, timeout under load.
- This ADR: **Accepted** (owner Teemo, 2026-09-10). Adapter implementation remains a later PR; this docs change does not ship `ollama.py`.

## Consequences

- Operators can opt into literary-quality local translation without paying OpenAI or sending story text to a third-party LLM API.
- Worker latency and RAM profile change under `ollama`; soft timeouts and concurrency (`WORKER_CONCURRENCY=1`) matter more.
- Quality still depends on model size/quantization and prompting; this ADR does not claim parity with every commercial ChatGPT tier.
- A second optional adapter (OpenAI) later should reuse the same port and cache rules without FSM/API changes.

## Alternatives (rejected for *first* optional LLM)

| Alternative | Why not first |
| --- | --- |
| OpenAI `gpt-4o-mini` (issue #12 original) | Paid tokens; wrong for current lab budget. Keep as a later adapter. |
| LibreTranslate / Argos | Free/light but typically weaker than NLLB for literary zh→vi — does not meet the quality bar. |
| Larger NLLB only (1.3B / 3.3B) | May reduce truncation; still NMT; 3.3B conflicts with ADR 0009 lab box. |
| Gemini / Groq / OpenRouter free tiers | Possible $0 cloud, but not self-hosted; rate limits and data leave the machine. Secondary to Ollama. |
| LTEngine / LibreTranslateLLM sidecar | Valid LibreTranslate-shaped wrapper over Ollama; more moving parts for lab — prefer direct Ollama HTTP from the worker first. |

## Out of scope

- Replacing NLLB as Compose/MVP default.
- Implementing OpenAI/Gemini/Ollama-remote-SaaS in the same PR as the first Ollama adapter (unless explicitly split behind separate provider ids).
- Changing job FSM, chunk identity scheme, narration, or TTS.
- Baking TranslateGemma weights into the API/worker image.
- Claiming GPU/`runtime: nvidia` in default Compose.

## Implementation checklist (for planning)

Ordered so Cursor can turn this **Accepted** ADR into a later adapter PR. Keep this checklist; do **not** implement `ollama.py` in the same change as this docs-only ADR.

1. **ADR land** — **done (Accepted).** File `docs/adr/0011-ollama-translategemma-optional-translation.md`; row in `docs/adr/README.md`; align issue #12.
2. **Settings + factory** — `OLLAMA_BASE_URL`, `OLLAMA_TRANSLATION_MODEL`; branch in DI when `TRANSLATION_PROVIDER=ollama`; ensure NLLB model is not constructed in that branch.
3. **Adapter** — HTTP client, BCP-47 mapping, supported language set, error mapping, translation-only prompting, size/chunk guards.
4. **Tests** — unit tests with fake HTTP/Ollama responses; optional `@pytest.mark.integration` against a live Ollama (skipped in CI by default).
5. **Docs / env** — `.env.example`, provider-development, README ops (pull model, base URL, timeouts, do not co-load NLLB).
6. **Manual lab acceptance** — same fixtures as M13 demo (`sample-zh-CN.txt` → `vi-VN`, etc.) with `TRANSLATION_PROVIDER=ollama`; compare truncation/name/idiom failures called out in #12.
7. **Follow-ups (separate)** — OpenAI adapter; optional Compose `ollama` service profile; stale-job/ops polish unrelated to this ADR.

## References

- Issue [#12](https://github.com/teemo2Xteemo/text-to-audiobook/issues/12) and lab free/local comment thread
- ADR 0001, 0002, 0005, 0006, 0008 (Edge TTS = first *optional-style* vendor adapter pattern for TTS), 0009, 0010
- `docs/ai/provider-development.md`
- Requirements §7 (optional LLM candidates), §34 (free-first MVP)
- [Ollama TranslateGemma library](https://ollama.com/library/translategemma)
