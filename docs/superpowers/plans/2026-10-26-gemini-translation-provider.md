# Google Gemini Translation Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a Google Gemini translation provider adapter (`gemini-2.0-flash` / `gemini-1.5-flash`) that offloads translation from local CPU to Google AI Studio's free tier for high-speed, human-like literary translation.

**Architecture:** A new adapter `GeminiTranslationProvider` implements the domain `TranslationProvider` port, communicating via async HTTP to Google Gemini's REST API (`generateContent`) using stdlib `urllib.request` in `asyncio.to_thread` (matching existing `UrllibOllamaHttpClient`). Settings, factory, and cache integration register `gemini` under `TRANSLATION_PROVIDER=gemini`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, urllib.request, pytest.

**Spec:** [docs/superpowers/specs/2026-10-26-gemini-translation-provider-design.md](file:///home/teemo/documents/projects/text-to-audiobook/docs/superpowers/specs/2026-10-26-gemini-translation-provider-design.md)

## Global Constraints

- **Language-Agnostic (ADR 0001):** Never hardcode language pairs in business logic; domain BCP-47 mapping stays inside the adapter.
- **Provider Port Isolation (ADR 0002):** Implement `TranslationProvider` protocol in `backend/app/domain/ports.py`; no vendor SDKs in domain or application layers.
- **Cache Key Identity (ADR 0006):** Cache keys include `provider="gemini"` and `model_id`.
- **CPU-First & Opt-In (ADR 0009 / ADR 0011):** Default Compose stays `fake` or `nllb`; `gemini` is an explicit opt-in provider.
- **Secrets Management:** `GEMINI_API_KEY` is loaded strictly from environment variables; never hardcode or commit keys.

## Review Focus

1. **Missing or blank API key:** Calling `translate()` with no `gemini_api_key` configured raises a typed `DomainError(ErrorType.TRANSLATION_FAILED, "GEMINI_API_KEY is not configured")`.
2. **Rate Limit 429 response:** When Gemini returns HTTP 429 (`RESOURCE_EXHAUSTED`), the adapter raises `DomainError(ErrorType.PROVIDER_RATE_LIMIT, ...)`.
3. **Safety filter block / empty candidates:** When Gemini blocks output due to safety filters (`finishReason` not STOP or empty parts), the adapter raises `DomainError(ErrorType.TRANSLATION_FAILED, ...)`.
4. **Auto source language tag:** Calling `translate()` with `source_language="auto"` raises `DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "auto is not a translation source; resolve it first")`.
5. **HTTP timeout / network connection failure:** When connection times out or fails, raises `DomainError(ErrorType.TIMEOUT, ...)`.

---

### Task 1: Settings and Configuration

**Files:**
- Modify: `backend/app/config/settings.py:51-68`
- Modify: `.env.example:15-30`
- Test: `backend/tests/test_settings.py:1-60`

**Interfaces:**
- Consumes: `pydantic_settings.BaseSettings`
- Produces: `Settings.gemini_api_key`, `Settings.gemini_translation_model`, `Settings.gemini_base_url`, `Settings.gemini_http_timeout_seconds`

- [x] **Step 1: Write the failing test for Gemini settings in `backend/tests/test_settings.py`**
- [x] **Step 2: Run test to verify it fails**
- [x] **Step 3: Implement Gemini fields and URL validator in `backend/app/config/settings.py`**
- [x] **Step 4: Run tests to verify they pass**
- [x] **Step 5: Commit**

```bash
git add backend/app/config/settings.py .env.example backend/tests/test_settings.py
git commit -m "feat(config): add gemini translation provider settings"
```

---

### Task 2: Core Gemini Translation Provider Adapter

**Files:**
- Create: `backend/app/providers/translation/gemini.py`
- Create: `backend/tests/providers/test_gemini_translation.py`

**Interfaces:**
- Consumes:
  - `app.domain.ports.TranslationProvider`
  - `app.domain.errors.DomainError`, `ErrorType`
  - `app.domain.languages.AUTO_SOURCE_LANGUAGE`
- Produces:
  - `GeminiTranslationProvider`:
    - `__init__(*, api_key: str | None, model: str, base_url: str, timeout_seconds: float, http: GeminiHttpClient | None = None)`
    - `async def translate(self, text: str, source_language: str, target_language: str) -> str`
    - `def supported_languages(self) -> Sequence[str]`

- [x] **Step 1: Write failing tests in `backend/tests/providers/test_gemini_translation.py`**
- [x] **Step 2: Run test to verify it fails**
- [x] **Step 3: Implement `GeminiTranslationProvider` and `UrllibGeminiHttpClient` in `backend/app/providers/translation/gemini.py`**
- [x] **Step 4: Run tests to verify they pass**
- [x] **Step 5: Commit**

```bash
git add backend/app/providers/translation/gemini.py backend/tests/providers/test_gemini_translation.py
git commit -m "feat(providers): implement gemini translation provider adapter"
```

---

### Task 3: Factory and Cache Integration

**Files:**
- Modify: `backend/app/config/factory.py:90-195`
- Test: `backend/tests/config/test_factory.py:1-120`

**Interfaces:**
- Consumes:
  - `backend.app.config.settings.Settings`
  - `backend.app.providers.translation.gemini.GeminiTranslationProvider`
- Produces:
  - `build_translation_provider(settings)` returns `GeminiTranslationProvider` when `settings.translation_provider == "gemini"`
  - `translation_pin_from_settings(settings)` returns `("gemini", settings.gemini_translation_model)`
  - `settings_for_translation_pin(settings, job)` restores gemini model pin
  - `cache_identity_from_settings(settings)` sets `translation_model = settings.gemini_translation_model`

- [x] **Step 1: Write failing tests in `backend/tests/config/test_factory.py`**
- [x] **Step 2: Run test to verify it fails**
- [x] **Step 3: Update `backend/app/config/factory.py` to register Gemini**
- [x] **Step 4: Run all tests to verify they pass**
- [x] **Step 5: Run linter and formatter**
- [x] **Step 6: Commit**

```bash
git add backend/app/config/factory.py backend/tests/config/test_factory.py
git commit -m "feat(factory): register gemini translation provider and cache identity"
```
