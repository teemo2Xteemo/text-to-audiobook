# Design: Translation Profile Selection on UI

- **Date:** 2026-10-26
- **Status:** Proposed
- **Author:** Antigravity / teemo

---

## 1. Intent and Why

### Context
Currently, the translation engine used by the system is fixed globally via the server's environment variable `TRANSLATION_PROVIDER` (e.g. `nllb`, `gemini`, `ollama`, or `fake`). When an external translation API experiences issues (such as Google Gemini quota exhaustion/429 rate limit, network disruption, or missing API keys), the end user cannot generate audiobooks and has no recourse without server operator intervention.

Furthermore, different stories demand different trade-offs:
- **Literary nuance & natural prose:** Cloud AI models like Gemini 3.5 Flash.
- **Speed & lower token cost:** Gemini 2.0 Flash.
- **Privacy & offline reliability:** Local CPU distilled NLLB-200.
- **Local self-hosted LLM:** Ollama TranslateGemma.

### Purpose
Expose a curated list of active **Translation Profiles** directly on the studio creation UI, letting users choose their preferred translation engine and fallback options dynamically on a per-job basis.

---

## 2. Goals, Non-Goals, and Anti-Goals

### Goals
- **Curated Profiles (Approach 2):** Group valid `(provider, model)` combinations into user-friendly translation profiles with descriptive badges (e.g. `"ONLINE"`, `"OFFLINE"`), human-readable labels, and purpose descriptions.
- **Server Readiness Filtering:** The backend only advertises profiles that are actually configured and ready to execute (e.g. Gemini profiles are only presented if a non-empty `GEMINI_API_KEY` is configured).
- **Per-Job Pinning:** Selected provider and model are pinned to the `Job` record (`job.translation_provider`, `job.translation_model`), ensuring chunk cache consistency (ADR 0006) and execution by the worker.
- **Backward Compatibility:** If `translation_profile_id` is omitted from `POST /api/jobs`, the system falls back to the server's default translation pin without error.

### Non-Goals
- **User-supplied API Keys:** End users cannot enter arbitrary personal API keys via the UI in this milestone; keys remain managed securely by the server environment.
- **Mid-pipeline Dynamic Fallback:** If a provider fails mid-story, chunks are not automatically routed to a different engine mid-job (as this would violate chunk caching identity and create discordant narrative styles in the same audiobook). Users re-submit or retry with another profile.
- **Arbitrary Model Text Input:** Freeform string model names are not accepted to prevent runtime 404s/crashes from mistyped identifiers.

### Anti-Goals
- **Hardcoded Frontend Lists:** The frontend must never hardcode the list of providers or models. All available options must be retrieved dynamically from `GET /api/capabilities`.
- **Breaking Existing CLI or API Clients:** Existing integration scripts calling `POST /api/jobs` without translation parameters must continue to work with server defaults.

---

## 3. Approaches Considered

### Approach 1: Hierarchical 2-Level Dropdown (Provider Selector → Model Selector)
- **Concept:** User first selects a provider (e.g. Google Gemini, NLLB, Ollama), which updates a secondary dropdown showing available models for that provider.
- **Trade-offs:** Adds extra form complexity and dependency state in the UI. For most audiobook creators, choosing between technical model names is less helpful than choosing between clear quality/purpose profiles.

### Approach 2: Curated Translation Profiles (1 Unified StudioSelect) — [SELECTED]
- **Concept:** Backend pre-packages validated provider + model combinations into descriptive profiles (e.g. *Gemini 3.5 Flash · Natural Prose*, *Gemini 2.0 Flash · Fast*, *NLLB-200 · Offline Privacy*). Frontend renders a single polished `StudioSelect` dropdown with badges and descriptions.
- **Trade-offs:** Highly intuitive, zero UI state coordination, minimal form clutter, while still allowing operators to configure exactly which models and providers are exposed.
- **Reason for Selection:** Chosen by user during brainstorming for superior usability and clean interface design.

---

## 4. Technical Design

### 4.1. Domain & Schema (`backend/app/domain`, `backend/app/api/schemas.py`)

#### Translation Profile Model
```python
@dataclass(frozen=True)
class TranslationProfile:
    id: str  # e.g. "gemini-3.5-flash", "gemini-2.0-flash", "nllb-600m"
    provider: str  # "gemini" | "nllb" | "ollama" | "fake"
    model: str  # specific model id
    label: str  # "Google Gemini 3.5 Flash"
    badge: str  # "ONLINE" | "OFFLINE" | "LOCAL"
    description: str  # "Natural prose · Recommended"
    is_default: bool
```

#### Capabilities Response
```python
class TranslationProfileResponse(BaseModel):
    id: str
    provider: str
    model: str
    label: str
    badge: str
    description: str
    is_default: bool

class CapabilitiesResponse(BaseModel):
    languages: list[str]
    voices: list[VoiceResponse]
    translation_profiles: list[TranslationProfileResponse]
```

#### Job Creation Request
```python
class CreateJobJsonBody(BaseModel):
    text: str
    source_language: str
    target_language: str
    voice: str | None = None
    speed: float = SPEED_DEFAULT
    output_format: str = Field(default=OutputFormat.MP3.value)
    translation_profile_id: str | None = None
```

### 4.2. Capabilities Discovery (`backend/app/application/capabilities.py`, `backend/app/config/factory.py`)

A factory helper `build_available_translation_profiles(settings: Settings) -> list[TranslationProfile]` inspects configuration synchronously without runtime network probes:

1. **Test Mode (`TRANSLATION_PROVIDER=fake`):**
   - Returns only the `fake` profile (`id="fake"`, `provider="fake"`, `model="fake"`, `badge="MOCK"`, `is_default=True`).
   - Keeps CI and automated tests lightweight, isolated, and free of GPU/CPU model loading.

2. **Standard Production / Lab Mode (`TRANSLATION_PROVIDER != fake`):**
   - **Gemini:** Included if `settings.gemini_api_key` has a non-empty secret value:
     - `gemini-3.5-flash`: Gemini 3.5 Flash (`gemini-3.5-flash`), badge `"ONLINE"`, description `"Văn phong tự nhiên · Khuyên dùng"`.
     - `gemini-2.0-flash`: Gemini 2.0 Flash (`gemini-2.0-flash`), badge `"ONLINE"`, description `"Tốc độ cao · Tiết kiệm quota"`.
   - **NLLB:** Always available in CPU / offline setup:
     - `nllb-600m`: NLLB-200 600M (`settings.nllb_model_id`), badge `"OFFLINE"`, description `"Chạy hoàn toàn trên máy · Riêng tư"`.
   - **Ollama:** Configuration-driven (to avoid stalling capabilities on offline daemons). Included when `settings.translation_provider == "ollama"`:
     - `ollama-gemma`: Ollama TranslateGemma (`settings.ollama_translation_model`), badge `"LOCAL"`, description `"Mô hình mã nguồn mở cục bộ"`.

3. **`is_default` Precedence Rule:**
   - Evaluated directly against `translation_pin_from_settings(settings)`:
     - `default_provider, default_model = translation_pin_from_settings(settings)`
     - The profile matching `profile.provider == default_provider` (and matching `profile.model == default_model` if applicable) receives `is_default = True`.
     - If no profile matches (or operator set an unlisted model), the primary recommended profile (Gemini 3.5 if present, else NLLB) receives `is_default = True`.
   - `JobService` uses the exact same fallback `(default_provider, default_model)` when `translation_profile_id` is omitted, guaranteeing 100% parity between UI and API default execution.

### 4.3. Job Service Resolution (`backend/app/application/jobs.py`)

In `JobService.create(command: CreateJobCommand) -> Job`:
1. If `command.translation_profile_id` is supplied:
   - Match against available profiles in `self._available_profiles`.
   - If not found: raise `DomainError(ErrorType.INVALID_INPUT, f"unknown translation profile: {command.translation_profile_id}")`.
   - Assign `translation_provider = profile.provider`, `translation_model = profile.model`.
2. If `command.translation_profile_id` is `None`:
   - Fall back to server defaults `self._translation_provider` and `self._translation_model` (from `translation_pin_from_settings(settings)`).

### 4.4. Frontend UI (`frontend/src/`)

1. **API Types (`frontend/src/api/types.ts`):**
   ```typescript
   export type TranslationProfile = {
     id: string;
     provider: string;
     model: string;
     label: string;
     badge: string;
     description: string;
     is_default: boolean;
   };

   export type Capabilities = {
     languages: string[];
     voices: Voice[];
     translation_profiles?: TranslationProfile[];
   };

   export type CreateJobInput = {
     // ... existing fields
     translation_profile_id?: string;
   };
   ```

2. **Job Form (`frontend/src/components/JobForm.tsx`):**
   - In Step 1 (Configure), render a dedicated `StudioSelect` labeled **Translation engine**:
     - Options are mapped from `capabilities.translation_profiles`.
     - Displays `label`, `badge`, and `description` via existing `StudioSelectOption` schema.
     - Defaults to the profile having `is_default: true` (or the first option).
   - Passed into `onSubmit` as `translation_profile_id`.

---

## 5. What's Left to the Builder
- Exact Vietnamese/English localization of badge tags (`ONLINE`, `OFFLINE`, `LOCAL`) and descriptions.
- Ordering of profiles in the dropdown (Recommended Gemini first, Offline NLLB second, etc.).
- Unit tests validating `GET /api/capabilities`, `POST /api/jobs` resolution, and UI rendering.
