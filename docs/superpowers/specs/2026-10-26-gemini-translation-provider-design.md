# Design Document: Google Gemini Translation Provider Adapter

**Date:** 2026-10-26  
**Status:** Pending User Approval  
**Scope:** Small Change  
**Author:** Pair Programming Session with User

---

## 1. Intent & The Why

Running local AI translation models (such as NLLB or TranslateGemma via Ollama) on CPU-only machines is extremely slow when processing typical story chunks (~1,000 words per chunk), creating massive bottlenecks and timeouts for audiobook generation. 

The operator specifically requires:
1. **High speed & low machine footprint:** Offloading translation compute to the cloud so CPU-only machines do not stall.
2. **Zero cost / Free Tier:** Utilizing a generous free-tier API that does not require credit card verification.
3. **Natural, human-like literary translation ("ưu tiên dịch tự nhiên như người"):** High-quality, literary prose translation suitable for audiobooks, preserving dialogue nuances, narrative tone, and proper pronouns, which traditional dictionary/machine translation (NMT) fails to achieve.

Google Gemini (via Google AI Studio free tier) satisfies all these requirements with `gemini-2.0-flash` / `gemini-1.5-flash`, offering up to 1,500 requests/day (~1.5 million words/day) at zero cost.

---

## 2. Goals, Non-Goals, & Anti-Goals

### Goals
- Implement a `GeminiTranslationProvider` adapter implementing the domain [`TranslationProvider`](file:///home/teemo/documents/projects/text-to-audiobook/backend/app/domain/ports.py#L10-L14) interface in `backend/app/providers/translation/gemini.py`.
- Communicate directly with Google Gemini REST API (`/v1beta/models/{model}:generateContent`) using `httpx.AsyncClient` without heavy external SDK bloat.
- Provide a specialized system prompt for literary storytelling translation (natural flow, character pronouns, faithful meaning without conversational filler or markdown code blocks).
- Make model name (`GEMINI_TRANSLATION_MODEL`), API key (`GEMINI_API_KEY`), and base URL (`GEMINI_BASE_URL`) fully configurable via environment variables and settings.
- Map domain BCP-47 language codes seamlessly.
- Properly map API responses and errors to typed domain errors (`PROVIDER_RATE_LIMIT` on 429, `TIMEOUT` on timeouts, `TRANSLATION_FAILED` on upstream failures).
- Register the adapter in `backend/app/config/factory.py` under `TRANSLATION_PROVIDER=gemini`.
- Support cache identity including provider ID (`gemini`) and model ID to prevent redundant API calls (per ADR 0006).

### Non-Goals
- Adding heavy third-party vendor SDKs (e.g. `google-generativeai` or `google-genai`). We use `httpx` directly, matching existing network adapters (such as Edge TTS and Ollama).
- Modifying core pipeline chunking, audio synthesis, or job state machine.
- Changing the Compose default (`fake` in development / `nllb` offline); Gemini is an explicit opt-in provider.

### Anti-Goals
- **No secrets in repository:** Never hardcode or commit API keys into source code or test fixtures.
- **No unhandled 500 crashes:** 429 rate limits and safety block filters must produce typed domain errors.
- **No hardcoded language pairs:** Prompts and adapter logic must remain strictly language-agnostic.

---

## 3. Constraints & Repository Rules

- **Language-Agnostic (ADR 0001):** Business logic and adapters must never hardcode source or target languages.
- **Provider Port Isolation (ADR 0002):** Orchestrator and worker interact solely through the `TranslationProvider` port.
- **Cache Key Identity (ADR 0006):** Cache keys must include `provider="gemini"` and `model_id`.
- **CPU-First & Opt-In (ADR 0009 & ADR 0011):** Local test suite and default compose boot offline; external cloud providers require explicit configuration.

---

## 4. Approaches Considered & Tradeoffs

During brainstorming, three core approaches were evaluated:

### Approach 1 (Selected): Google Gemini REST Adapter (`gemini-2.0-flash`)
- **Mechanism:** Direct async HTTP requests to Google AI Studio Gemini API with literary translation system instruction.
- **Tradeoffs:** 
  - *Pros:* 100% free tier without credit card requirement, generous limits (1,500 requests/day, 1M tokens/min), fast latency (1-2s for 1k words), outstanding natural Vietnamese literary translation.
  - *Cons:* Uses Google-specific REST payload schema (`contents`, `parts`, `systemInstruction`).
- **Why chosen:** Best fit for the user's primary criteria: zero cost, CPU offload, and natural human-like storytelling translation.

### Approach 2: Generic OpenAI-Compatible Adapter via Groq Cloud
- **Mechanism:** Generic `/v1/chat/completions` adapter pointed at Groq (e.g., Llama 3.3 70B).
- **Tradeoffs:**
  - *Pros:* Standardized OpenAI protocol.
  - *Cons:* Groq free tier has strict daily token limits (100k tokens/day for 70B), and Llama translation quality for Vietnamese literature is more rigid and literal than Gemini Flash.

### Approach 3: Traditional Cloud NMT (Azure Translator / Google Cloud Translation / DeepL)
- **Mechanism:** Classical machine translation APIs.
- **Tradeoffs:**
  - *Pros:* Fast, deterministic.
  - *Cons:* DeepL and Azure require credit cards; monthly caps are small (500k chars/month); translations are phrase-by-phrase literal and sound unnatural for audiobooks.

---

## 5. Implementation Details

1. **Adapter implementation (`backend/app/providers/translation/gemini.py`):**
   - Class `GeminiTranslationProvider`:
     - Method `translate(text, source_language, target_language) -> str`
     - Method `supported_languages() -> Sequence[str]` (supports all standard languages or dynamic validation).
   - Payload:
     ```json
     {
       "contents": [{"role": "user", "parts": [{"text": "<input_text>"}]}],
       "systemInstruction": {
         "parts": [{
           "text": "You are a professional literary translator. Translate the text from {source_name} to {target_name}. Preserve tone, nuance, character dialogue style, and narrative flow. Return ONLY the translated text without explanations, greetings, or formatting markdown."
         }]
       },
       "generationConfig": {
         "temperature": 0.3
       }
     }
     ```
2. **Settings (`backend/app/config/settings.py`):**
   - `gemini_api_key: str | None = None`
   - `gemini_translation_model: str = "gemini-2.0-flash"`
   - `gemini_base_url: str = "https://generativelanguage.googleapis.com"`
3. **Factory Registration (`backend/app/config/factory.py`):**
   - In `get_translation_provider()`: branch `if settings.translation_provider == "gemini":` instantiate `GeminiTranslationProvider`.
4. **Environment (`.env.example`):**
   - Add `GEMINI_API_KEY=` and `GEMINI_TRANSLATION_MODEL=gemini-2.0-flash`.
5. **Testing:**
   - Unit tests in `backend/tests/providers/translation/test_gemini.py` mocking the Gemini HTTP endpoint (success, 429 rate limit, 500 error, safety filter block, empty response).

---

## 6. What's Left to the Builder

- Exact HTTP status code handling and retry-after header parsing if 429 is encountered.
- Safety rating threshold settings in Gemini payload to prevent benign fictional story excerpts from being falsely blocked.
