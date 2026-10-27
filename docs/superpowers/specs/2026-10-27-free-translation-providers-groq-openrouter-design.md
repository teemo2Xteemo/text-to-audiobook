# Design Document: Free Translation Providers (Groq & OpenRouter with Qwen/Llama)

**Date:** 2026-10-27  
**Status:** Pending User Approval  
**Scope:** Small Change  
**Author:** Pair Programming Session with User  

---

## 1. Intent & The Why

When translating novels and stories (particularly Chinese to Vietnamese `zh` → `vi`), operators encountered immediate rate limiting on Google AI Studio (`429 RESOURCE_EXHAUSTED` / `PROVIDER_RATE_LIMIT`) after translating only a few chunks. This occurs because Google AI Studio free tier enforces low burst limits (15 RPM on Flash, or 2 RPM on Pro), while audiobook pipelines process multiple story chunks in rapid succession.

To solve this, the operator requires:
1. **High Free-Tier Limits:** High RPM and RPD to translate entire chapters/books without hitting 429 bottlenecks.
2. **Superior Asian Language Translation (Chinese → Vietnamese):** High literary quality preserving Sino-Vietnamese vocabulary (từ Hán-Việt), idioms, dialogue tone, and character pronouns (huynh/muội, bản tọa, nàng/hắn), where Qwen models significantly outperform Western-centric models.
3. **Resilience & Redundancy:** A primary provider for high-speed translation (Groq) with an instant fallback to top-tier free community models (OpenRouter) when needed.

---

## 2. Goals, Non-Goals, & Anti-Goals

### Goals
- Implement a reusable, standard OpenAI-compatible translation provider adapter (`OpenAITranslationProvider` or specific Groq/OpenRouter adapters) in `backend/app/providers/translation/` using Python stdlib `urllib` (zero new external dependencies).
- Support **Groq** as an opt-in provider (`TRANSLATION_PROVIDER=groq`) with default models `qwen/qwen3.8-27b` and `llama-3.3-70b-versatile` / `llama-3.1-8b-instant`.
- Support **OpenRouter** as an opt-in provider (`TRANSLATION_PROVIDER=openrouter`) with free models such as `qwen/qwen-2.5-72b-instruct:free`.
- Expose corresponding profiles in `build_available_translation_profiles()` so users can switch between Groq and OpenRouter seamlessly from the UI.
- Preserve domain error mapping (HTTP 429 → `PROVIDER_RATE_LIMIT`, 401/403 → `PROVIDER_CONFIGURATION_ERROR`, 5xx → `TRANSLATION_FAILED`, timeout → `TIMEOUT`).
- Maintain cache keys including `provider` and `model` (per ADR 0006).
- Comprehensive unit tests covering payload serialization, error mapping, and language compatibility.

### Non-Goals
- Installing heavy third-party vendor SDKs (`groq`, `openai`). The adapter uses Python's standard library `urllib` / `asyncio.to_thread` directly, identical to `UrllibGeminiHttpClient`.
- Changing existing core chunking or narration logic.
- Replacing existing offline providers (`nllb`, `ollama`) or `gemini`.

### Anti-Goals
- No hardcoded API keys or secrets in source code or git.
- No hardcoded source/target languages in business logic (maintaining ADR 0001).

---

## 3. Constraints & Repository Rules

- **Language-Agnostic (ADR 0001):** System prompts dynamically interpolate source and target language names; no language pair is hardcoded.
- **Provider Port Isolation (ADR 0002):** Adapters strictly implement `TranslationProvider` defined in `backend/app/domain/ports.py`.
- **Cache Strategy (ADR 0006):** Artifact cache keys include provider and model identity so chunks translated by Groq or OpenRouter are independently cached and reusable.
- **Lightweight Dependencies:** Keep image and runtime footprint minimal by avoiding unnecessary pip dependencies.

---

## 4. Approaches Considered & Tradeoffs

1. **Approach 1: Groq API only**
   - *Pros:* 30 RPM, fast inference (~300-500 t/s), stable.
   - *Cons:* Subject to daily token caps (1,000 RPD on 70B).
2. **Approach 2: OpenRouter Free only**
   - *Pros:* Free access to 72B models (`qwen-2.5-72b-instruct:free`).
   - *Cons:* Rate limits and latency can spike during peak hours.
3. **Approach 3: Gemini Throttling / Rate-Limiting only**
   - *Pros:* No new accounts needed.
   - *Cons:* Squeezes throughput to 1 request every 4 seconds (15 RPM max), and Gemini Vietnamese literary nuance on Chinese web novels is less natural than Qwen.
4. **Approach 4 (Selected): Combined Groq + OpenRouter (Unified OpenAI-Compatible Adapter)**
   - *Pros:* Both providers adhere 100% to the OpenAI Chat Completions API format (`/chat/completions`). A single robust adapter handles both endpoints seamlessly. The user gets high-speed Groq by default, with OpenRouter as high-capacity backup on the UI.
   - *Why chosen:* Solves quota limits permanently while giving the highest translation quality for Chinese → Vietnamese stories.

---

## 5. Technical Specification

### 5.1 Architecture & Adapters
Create `backend/app/providers/translation/openai_compatible.py` containing:
- `OpenAICompatibleHttpClient`: Sends JSON POST requests to `{base_url}/chat/completions` with `Authorization: Bearer {api_key}`.
- `OpenAICompatibleTranslationProvider`:
  - Translates text chunks with literary storytelling system instruction.
  - Temperature set to 0.3 for consistency.
  - Supports BCP-47 language codes mapping.
  - Strips any occasional markdown code block fences (` ``` `).
  - Translates upstream errors into `DomainError`.

Provide convenience subclasses or factory instances:
- `GroqTranslationProvider`:
  - Default `base_url`: `https://api.groq.com/openai/v1`
  - Default `model`: `qwen/qwen3.8-27b` (or `llama-3.3-70b-versatile`)
- `OpenRouterTranslationProvider`:
  - Default `base_url`: `https://openrouter.ai/api/v1`
  - Default `model`: `qwen/qwen-2.5-72b-instruct:free`
  - Headers include `HTTP-Referer` and `X-Title` (optional OpenRouter best practice).

### 5.2 Settings & Configuration
In `backend/app/config/settings.py`:
- `groq_api_key: SecretStr | None = Field(default=None)`
- `groq_translation_model: str = Field(default="qwen/qwen3.8-27b")`
- `groq_base_url: str = Field(default="https://api.groq.com/openai/v1")`
- `openrouter_api_key: SecretStr | None = Field(default=None)`
- `openrouter_translation_model: str = Field(default="qwen/qwen-2.5-72b-instruct:free")`
- `openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")`

### 5.3 Factory & Profiles
In `backend/app/config/factory.py`:
- Register `groq` and `openrouter` in `build_translation_provider`.
- Register translation profiles in `build_available_translation_profiles(settings)`:
  - If `groq_api_key` is present:
    - `groq-qwen`: "Groq (Qwen 27B / Chinese-Vietnamese Specialist)", badge `ONLINE`
    - `groq-llama-70b`: "Groq (Llama 3.3 70B)", badge `ONLINE`
    - `groq-llama-8b`: "Groq (Llama 3.1 8B Instant)", badge `ONLINE`
  - If `openrouter_api_key` is present:
    - `openrouter-qwen-72b`: "OpenRouter (Qwen 2.5 72B Free)", badge `ONLINE`

---

## 6. What's Left to the Builder

- Implement `OpenAICompatibleTranslationProvider`, `GroqTranslationProvider`, `OpenRouterTranslationProvider`.
- Update `Settings` and `factory.py`.
- Write unit tests in `backend/tests/providers/test_groq_openrouter.py`.
- Verify full test suite passes with `pytest`.
