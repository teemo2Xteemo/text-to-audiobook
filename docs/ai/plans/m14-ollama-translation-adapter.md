# Implementation plan: Ollama + TranslateGemma translation adapter

- Status: Contract pinned (post-M13 optional provider). Adapter/factory/settings/Compose `OLLAMA_*` passthrough/tests are **not** on `main` as of the 2026-09-13 read-only check; lab smoke not verified. Do **not** insert M14 into the M1–M13 sequence.
- Depends on: **ADR 0011 Accepted** ([PR #15](https://github.com/teemo2Xteemo/text-to-audiobook/pull/15), merged)
- Tracks: [Issue #12](https://github.com/teemo2Xteemo/text-to-audiobook/issues/12)
- Milestone feel: post-M13 optional provider (do **not** change Compose boot default away from `fake`; NLLB remains the real-translation MVP default when operators opt in)

## Goal

Add `TRANSLATION_PROVIDER=ollama` that calls a local Ollama HTTP API with a TranslateGemma (or compatible) model, so lab operators get literary-quality translation **without OpenAI tokens**, while `nllb` / `fake` remain the offline/MVP paths.

## Non-goals

- Replacing Compose boot default (`fake`) or NLLB as the real-translation MVP default when operators opt in
- OpenAI / Gemini adapters (later PRs)
- FSM, chunking identity, narration, TTS changes
- Baking model weights into Docker images
- Co-loading NLLB weights when provider is `ollama`
- GPU / `runtime: nvidia` in default Compose

## Current code anchors (do not reinvent)

| Area | Location | Notes |
| --- | --- | --- |
| Port | `backend/app/domain/ports.py` → `TranslationProvider` | `async translate(...)`, `supported_languages()` |
| NLLB pattern | `backend/app/providers/translation/nllb.py` | Injected `NllbEngine` Protocol for tests; BCP-47 map inside adapter; rejects `auto` source |
| Fake | `backend/app/providers/translation/fake.py` | Offline stub |
| DI | `backend/app/config/factory.py` → `build_translation_provider` | Branch on `settings.translation_provider`; lazy-import heavy adapters |
| Cache identity | `cache_identity_from_settings` | `nllb` → `nllb_model_id`; `ollama` → `ollama_translation_model`; else `"fake"` |
| Settings | `backend/app/config/settings.py` | Add Ollama fields alongside `nllb_model_id` |
| Factory tests | `backend/tests/config/test_factory.py` | Mirror `test_factory_builds_nllb_provider_without_loading_weights` |
| Env docs | `.env.example`, `docs/ai/provider-development.md`, README | Names only; no secrets |

## Design

### Settings (new)

| Field / env | Type | Default | Purpose |
| --- | --- | --- |
| `ollama_base_url` / `OLLAMA_BASE_URL` | `str` | `http://127.0.0.1:11434` | Ollama HTTP base (no trailing slash required; normalize in adapter) |
| `ollama_translation_model` / `OLLAMA_TRANSLATION_MODEL` | `str` | `translategemma:4b` | Model tag; lab may use `translategemma:12b` |
| `ollama_http_timeout_seconds` / `OLLAMA_HTTP_TIMEOUT_SECONDS` | `float` | `120` (or similar ≥30) | Per-request HTTP timeout (distinct from `RQ_JOB_TIMEOUT_SECONDS`) |

Do **not** add API keys for local Ollama in this PR.

### Factory

In `build_translation_provider`:

```text
fake → FakeTranslationProvider
nllb → NllbTranslationProvider(model_id=settings.nllb_model_id)  # unchanged; do not construct when ollama
ollama → OllamaTranslationProvider(base_url=..., model=..., timeout=...)
else → UnknownProviderError
```

In `cache_identity_from_settings`:

```text
if translation == "nllb": model = settings.nllb_model_id
elif translation == "ollama": model = settings.ollama_translation_model
else: model = "fake"
```

Do not use the translation provider name as the model id, and do not raise from the cache helper for an unknown provider (factory `build_translation_provider` still raises `UnknownProviderError`). Changing `OLLAMA_TRANSLATION_MODEL` must change cache keys (ADR 0006).

### Adapter module

**Path:** `backend/app/providers/translation/ollama.py`

**Public class:** `OllamaTranslationProvider`

Suggested constructor:

```python
def __init__(
    self,
    *,
    base_url: str,
    model: str,
    timeout_seconds: float,
    http: OllamaHttpClient | None = None,  # Protocol for unit tests
) -> None: ...
```

**Responsibilities**

1. `supported_languages()` — return the **same domain BCP-47 keys** as NLLB (`nllb.py` `_BCP47_TO_FLORES`). Unsupported BCP-47 → `UNSUPPORTED_LANGUAGE`.
2. `translate` — reject `source_language == auto` with the same message pattern as NLLB (`auto is not a translation source; resolve it first`).
3. Map domain BCP-47 → prompt language codes **inside the adapter** (`backend/app/providers/translation/ollama.py`). See **Pinned BCP-47** below.
4. Call Ollama over HTTP (prefer `/api/chat` with the model’s chat template / language-code fields if required; otherwise a single user message that requests translation-only output). **No** Ollama Python SDK in domain/application/routes; stdlib or existing HTTP stack only (e.g. `urllib` / `httpx` if already a dependency — do not add heavy deps without need).
5. Strip commentary; return translation text only. If the model wraps output in markdown fences, strip them defensively.
6. Map errors:
   - connection / DNS / refused → `TRANSLATION_FAILED` (message safe, no secrets)
   - HTTP timeout → `TIMEOUT`
   - HTTP 4xx/5xx / empty body → `TRANSLATION_FAILED`
   - rate-limit style 429 if ever seen → `PROVIDER_RATE_LIMIT`
7. Guard input size: fail when `len(text) > 8000` with `TRANSLATION_FAILED` / `"text exceeds model context"`. Do **not** use `len(text) // 4` (or any other token heuristic) for this guard. Pipeline already chunks; adapter is a last line of defense. No silent truncation.
8. Logging: `provider=ollama`, `model=...`, languages, `character_count` — never log full story text or URLs with credentials.

**HTTP client Protocol** (for fakes):

```python
class OllamaHttpClient(Protocol):
    async def chat(self, *, model: str, messages: list[dict[str, Any]]) -> str: ...
```

Production client performs POST `{base}/api/chat` with `stream: false`, parses JSON message content.

### Pinned BCP-47 (adapter-only)

Same domain keys as NLLB. Prompt codes are ISO 639-1 except Chinese script tags. Implement the map in `backend/app/providers/translation/ollama.py`.

| Domain (BCP-47) | Prompt code |
| --- | --- |
| `zh-Hans` | `zh-Hans` |
| `zh-Hant` | `zh-Hant` |
| `zh-CN`, `zh-SG` | `zh-Hans` |
| `zh-TW`, `zh-HK` | `zh-Hant` |
| `bn-BD` | `bn` |
| `ms-MY` | `ms` |
| `nb-NO` | `nb` |
| other NLLB keys (e.g. `vi-VN`, `en-US`) | ISO 639-1 primary subtag (`vi`, `en`, …) |

### Pinned input guard

Fail when `len(text) > 8000` with `ErrorType.TRANSLATION_FAILED` and message `"text exceeds model context"`. Do **not** estimate tokens with `len(text) // 4`.

### Prompt / template contract

- Prefer TranslateGemma’s documented language-code / chat-template fields when using Ollama’s chat API.
- Fallback user content shape (only if template fields unavailable): instruct “translate from {src} to {tgt}; output only the translation” — **do not** hard-code zh→vi as architecture; languages come from arguments.
- Temperature low / deterministic if the API allows options.

### Compose / ops

- Default Compose **boot** provider stays `fake` (`docker-compose.yml`: `TRANSLATION_PROVIDER: ${TRANSLATION_PROVIDER:-fake}` on **api** and **worker**). Do **not** add an `ollama` Compose service.
- Passthrough of `OLLAMA_BASE_URL`, `OLLAMA_TRANSLATION_MODEL`, and `OLLAMA_HTTP_TIMEOUT_SECONDS` on **api + worker** is in scope (capabilities and the worker share the same adapter config). Values come from `.env`; Compose must not hard-code `TRANSLATION_PROVIDER=ollama`.
- Document “bring your own Ollama” on host: `ollama pull translategemma:4b` (or `:12b`), set env, restart **worker** (and API if capabilities list languages from provider).
- Optional follow-up (out of this adapter): Compose profile/service `ollama` — not required for MVP of the adapter (ADR 0011 item 7).
- When `TRANSLATION_PROVIDER=ollama`, worker must **not** import/load `TransformersNllbEngine` (factory already lazy-imports NLLB only on `nllb` branch — keep it that way).
- Remind operators: raise `RQ_JOB_TIMEOUT_SECONDS` if long chapters + slow CPU; per-request `OLLAMA_HTTP_TIMEOUT_SECONDS` is separate.

## File-by-file checklist

### Code

- [ ] `backend/app/config/settings.py` — three new fields
- [ ] `backend/app/config/factory.py` — `ollama` branch + `cache_identity_from_settings` model selection
- [ ] `backend/app/providers/translation/ollama.py` — provider + HTTP client + BCP-47 map
- [ ] `backend/app/providers/translation/__init__.py` — export only if the package already re-exports peers (match existing style; often empty)

### Tests

- [ ] `backend/tests/providers/test_ollama_translation.py` (new)
  - supported language / unsupported language
  - rejects `auto` source
  - happy path: fake HTTP returns text → `translate` returns stripped text
  - timeout → `ErrorType.TIMEOUT`
  - connection error → `TRANSLATION_FAILED`
  - empty model response → `TRANSLATION_FAILED`
  - oversized input guard
- [ ] `backend/tests/config/test_factory.py`
  - builds ollama provider without network
  - `cache_identity_from_settings` uses `ollama_translation_model` when provider is ollama
  - unknown provider still fails
- [ ] `backend/tests/test_settings.py` — env parsing for new vars
- [ ] `backend/tests/test_env_example.py` — assert new names appear in `.env.example`
- [ ] Optional `@pytest.mark.integration` live Ollama test (skipped in CI by default)

### Docs

- [ ] `.env.example` — commented block for Ollama
- [x] `docs/ai/provider-development.md` — already mentions Ollama first optional; add implementer pointer if needed
- [ ] README troubleshooting — Ollama not running / model not pulled / timeout
- [x] Link this plan from issue #12 comment when PR opens

## Suggested implementation order

1. Settings + `.env.example` + settings/env tests  
2. `OllamaTranslationProvider` + fake HTTP + unit tests  
3. Factory wiring + cache identity + factory tests  
4. README troubleshooting  
5. Manual lab smoke (below)  
6. Open PR titled like `feat: optional Ollama TranslateGemma translation provider (ADR 0011)`

## Manual lab acceptance

On a machine with Ollama + pulled model (~16 GB RAM: prefer `:4b` first):

```bash
# host
ollama pull translategemma:4b

# .env (worker)
TRANSLATION_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434   # or 172.17.0.1 / host LAN IP from Linux
OLLAMA_TRANSLATION_MODEL=translategemma:4b
OLLAMA_HTTP_TIMEOUT_SECONDS=120
# leave TTS as edge or fake for faster checks
```

1. `docker compose up --build` (or restart worker after env change).  
2. Confirm `GET /api/capabilities` lists languages from the Ollama adapter (not NLLB-only).  
3. Upload/paste `backend/tests/fixtures/sample-zh-CN.txt`, target `vi-VN` — job reaches `completed`; translation should not truncate mid-story the way NLLB 600M did on #12.  
4. Spot-check names/idioms vs the failure examples in #12 (not expected to match ChatGPT exactly; must be clearly better than the broken NLLB demo).  
5. Confirm Redis/FS cache: second identical chunk is a cache hit (provider `ollama` + model id in key).  
6. Stop Ollama → job fails with typed translation/timeout error, not a stuck non-terminal status.

## PR acceptance criteria

- Unit tests green (`pytest -m "not integration"`)
- No torch/transformers import on `ollama` path
- Compose default still boots offline with `fake`/`nllb`
- ADR 0011 checklist items 2–6 covered; item 7 (OpenAI, Compose ollama service) left as follow-ups
- Issue #12 referenced in PR body

## Follow-ups (separate PRs)

- OpenAI adapter on the same port
- Optional Compose `ollama` service/profile
- Broader BCP-47 coverage / glossary hooks if literary QA demands it
