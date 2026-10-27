# Free Translation Providers (Groq & OpenRouter) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement OpenAI-compatible translation provider adapters supporting Groq (Qwen/Llama) and OpenRouter (Qwen 72B Free) to resolve AI Studio rate-limit issues, especially for Chinese to Vietnamese story translation.

**Architecture:** Create a unified `OpenAICompatibleTranslationProvider` in `backend/app/providers/translation/openai_compatible.py` using Python standard library `urllib` without external SDKs. Expose `GroqTranslationProvider` and `OpenRouterTranslationProvider`, configure runtime settings in `settings.py`, and register provider instances and translation profiles in `factory.py`.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic BaseSettings, pytest, standard library `urllib.request`.

**Spec:** [docs/superpowers/specs/2026-10-27-free-translation-providers-groq-openrouter-design.md](file:///home/teemo/documents/projects/text-to-audiobook/docs/superpowers/specs/2026-10-27-free-translation-providers-groq-openrouter-design.md)

## Global Constraints

- **Language-Agnostic (ADR 0001):** Business logic and adapters must never hardcode source or target languages; dynamic language mappings interpolate into prompt instructions.
- **Provider Port Isolation (ADR 0002):** Orchestrator and worker interact exclusively through the domain `TranslationProvider` port.
- **Zero Third-Party Vendor SDKs:** Use Python standard library `urllib.request` / `urllib.error` wrapped with `asyncio.to_thread` (matching `UrllibGeminiHttpClient`), avoiding bloated `openai` or `groq` dependencies.
- **Cache Key Identity (ADR 0006):** Cache keys must include provider identifier (`groq`, `openrouter`) and model identifier.
- **Typed Domain Errors:** Upstream HTTP 429 maps to `PROVIDER_RATE_LIMIT`, 401/403 to `PROVIDER_CONFIGURATION_ERROR`, 5xx to `TRANSLATION_FAILED`, and timeouts to `TIMEOUT`.

## Review Focus

1. **HTTP 429 Rate Limit Response:** Upstream returns status 429 (Too Many Requests); provider must raise `DomainError` with `ErrorType.PROVIDER_RATE_LIMIT` instead of unhandled crash.
2. **Markdown Code Fences in Output:** Model wraps translated text in ```` ``` ```` fences; provider must strip surrounding fences cleanly without altering translated content.
3. **Missing or Empty API Key:** Provider initialized without API key or with whitespace-only key must raise `DomainError(ErrorType.PROVIDER_CONFIGURATION_ERROR)`.
4. **Auto Source Language:** Attempting to call `.translate()` with source language `"auto"` must raise `DomainError(ErrorType.UNSUPPORTED_LANGUAGE)` before any network call.
5. **OpenRouter Specific Headers:** OpenRouter requests must properly attach `HTTP-Referer` and `X-Title` headers without breaking generic OpenAI or Groq calls.

---

### Task 1: OpenAI-Compatible Translation Provider Adapter

**Files:**
- Create: `backend/app/providers/translation/openai_compatible.py`
- Test: `backend/tests/providers/test_openai_compatible_translation.py`

**Interfaces:**
- Consumes:
  - `app.domain.ports.TranslationProvider`
  - `app.domain.errors.DomainError`, `ErrorType`
  - `app.domain.languages.AUTO_SOURCE_LANGUAGE`
- Produces:
  - `OpenAICompatibleHttpClient` (Protocol)
  - `UrllibOpenAIHttpClient`
  - `OpenAICompatibleTranslationProvider`
  - `GroqTranslationProvider`
  - `OpenRouterTranslationProvider`
  - `bcp47_to_openai_lang(language: str) -> tuple[str, str]`
  - `SUPPORTED_LANGUAGES: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests for OpenAI-compatible adapter**

Create `backend/tests/providers/test_openai_compatible_translation.py` testing:
- BCP-47 language mappings for valid and unsupported languages.
- Successful translation round-trip and payload format (`/chat/completions`, system message, user message).
- Stripping markdown fences (` ``` `).
- Error mappings (429 -> `PROVIDER_RATE_LIMIT`, 401/403 -> `PROVIDER_CONFIGURATION_ERROR`, 500/503 -> `TRANSLATION_FAILED`, timeout -> `TIMEOUT`).
- Rejecting `AUTO_SOURCE_LANGUAGE` and empty API key.
- Groq and OpenRouter subclasses providing their respective default `base_url` and headers.

- [ ] **Step 2: Run test to verify it fails**

Run: `./backend/.venv/bin/pytest backend/tests/providers/test_openai_compatible_translation.py -v`  
Expected: FAIL (ModuleNotFoundError: No module named 'app.providers.translation.openai_compatible')

- [ ] **Step 3: Implement `openai_compatible.py`**

Implement:
- Protocol `OpenAICompatibleHttpClient` and class `UrllibOpenAIHttpClient`.
- Language mapping table covering standard BCP-47 codes (including `zh-CN`, `zh-TW`, `vi-VN`, `en-US`, `ja-JP`, `ko-KR`, etc.).
- `OpenAICompatibleTranslationProvider` implementing `translate(text, source_language, target_language)` and `supported_languages()`.
- Error mapping function converting `urllib.error.HTTPError`, `URLError`, and `TimeoutError` into `DomainError`.
- Subclasses `GroqTranslationProvider` and `OpenRouterTranslationProvider` with their respective defaults.

- [ ] **Step 4: Run test to verify it passes**

Run: `./backend/.venv/bin/pytest backend/tests/providers/test_openai_compatible_translation.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/providers/translation/openai_compatible.py backend/tests/providers/test_openai_compatible_translation.py
git commit -m "feat(translation): add OpenAI-compatible provider adapter for Groq and OpenRouter"
```

---

### Task 2: Groq & OpenRouter Configuration Settings

**Files:**
- Modify: `backend/app/config/settings.py`
- Test: `backend/tests/test_settings.py`

**Interfaces:**
- Consumes:
  - `pydantic.SecretStr`, `Field`, `field_validator`
- Produces:
  - `Settings.groq_api_key: SecretStr | None`
  - `Settings.groq_translation_model: str`
  - `Settings.groq_base_url: str`
  - `Settings.groq_http_timeout_seconds: float`
  - `Settings.openrouter_api_key: SecretStr | None`
  - `Settings.openrouter_translation_model: str`
  - `Settings.openrouter_base_url: str`
  - `Settings.openrouter_http_timeout_seconds: float`

- [ ] **Step 1: Write failing tests for Groq and OpenRouter settings**

In `backend/tests/test_settings.py`:
- Add test verifying default values:
  - `groq_base_url == "https://api.groq.com/openai/v1"`
  - `groq_translation_model == "qwen/qwen3.8-27b"`
  - `openrouter_base_url == "https://openrouter.ai/api/v1"`
  - `openrouter_translation_model == "qwen/qwen-2.5-72b-instruct:free"`
- Add test verifying URL validation rejects non-HTTP schemes for `groq_base_url` and `openrouter_base_url`.

- [ ] **Step 2: Run test to verify it fails**

Run: `./backend/.venv/bin/pytest backend/tests/test_settings.py -k "groq or openrouter" -v`  
Expected: FAIL with AttributeError or ValidationError

- [ ] **Step 3: Implement settings fields and validators in `backend/app/config/settings.py`**

Add:
- `groq_api_key`, `groq_translation_model`, `groq_base_url`, `groq_http_timeout_seconds`.
- `openrouter_api_key`, `openrouter_translation_model`, `openrouter_base_url`, `openrouter_http_timeout_seconds`.
- Field validators ensuring `groq_base_url` and `openrouter_base_url` start with `http://` or `https://`.

- [ ] **Step 4: Run test to verify it passes**

Run: `./backend/.venv/bin/pytest backend/tests/test_settings.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config/settings.py backend/tests/test_settings.py
git commit -m "feat(config): add Groq and OpenRouter settings and URL validators"
```

---

### Task 3: Factory Integration & Profile Registration

**Files:**
- Modify: `backend/app/config/factory.py`
- Test: `backend/tests/config/test_factory_profiles.py` (or extend `backend/tests/config/test_settings.py` / factory tests)

**Interfaces:**
- Consumes:
  - `app.config.settings.Settings`
  - `app.providers.translation.openai_compatible.GroqTranslationProvider`
  - `app.providers.translation.openai_compatible.OpenRouterTranslationProvider`
  - `app.domain.translation_profile.TranslationProfile`
- Produces:
  - `build_translation_provider(settings)` supporting `"groq"` and `"openrouter"`
  - `translation_pin_from_settings(settings)` supporting `"groq"` and `"openrouter"`
  - `build_available_translation_profiles(settings)` exposing Groq & OpenRouter profiles

- [ ] **Step 1: Write failing tests for factory provider and profiles**

Create/extend factory tests testing:
- `build_translation_provider` raises `ProviderConfigurationError` if `GROQ_API_KEY` missing when `TRANSLATION_PROVIDER=groq`.
- `build_translation_provider` returns `GroqTranslationProvider` when key is supplied.
- Same for `openrouter` with `OPENROUTER_API_KEY`.
- `build_available_translation_profiles` adds `groq-qwen`, `groq-llama-70b`, `groq-llama-8b` when `groq_api_key` is present.
- `build_available_translation_profiles` adds `openrouter-qwen-72b` when `openrouter_api_key` is present.
- Languages supported by Groq and OpenRouter are correctly reported.

- [ ] **Step 2: Run test to verify it fails**

Run: `./backend/.venv/bin/pytest backend/tests/ -k "factory_profiles or groq_factory" -v`  
Expected: FAIL

- [ ] **Step 3: Update `backend/app/config/factory.py`**

- In `_languages_for_provider`: add `"groq"` and `"openrouter"` returning `SUPPORTED_LANGUAGES`.
- In `build_translation_provider`: instantiate `GroqTranslationProvider` when `name == "groq"` and `OpenRouterTranslationProvider` when `name == "openrouter"`.
- In `translation_pin_from_settings`: add `"groq"` and `"openrouter"` mapping to their configured models.
- In `build_available_translation_profiles`: dynamically append Groq profiles (Qwen, Llama 70B, Llama 8B) when `groq_key` is set, and OpenRouter profile (Qwen 72B Free) when `openrouter_key` is set.

- [ ] **Step 4: Run test to verify it passes**

Run: `./backend/.venv/bin/pytest backend/tests/ -k "factory or profiles" -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/config/factory.py backend/tests/
git commit -m "feat(factory): register Groq and OpenRouter providers and translation profiles"
```

---

### Task 4: Full Test Suite Verification

**Files:**
- Test: All unit and integration tests across backend

- [ ] **Step 1: Run the full backend pytest test suite**

Run: `./backend/.venv/bin/pytest backend/tests/ -v`  
Expected: All tests PASS with 0 errors.

- [ ] **Step 2: Commit any final test cleanups or documentation references**

```bash
git commit --allow-empty -m "chore(test): verify all unit tests pass with Groq and OpenRouter providers"
```
