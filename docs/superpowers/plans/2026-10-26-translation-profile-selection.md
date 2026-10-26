# Translation Profile Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable users to choose their preferred translation profile (curated provider + model combinations like Gemini 3.5 Flash, Gemini 2.0 Flash, NLLB-200 Offline, or Ollama) directly on the creation UI, falling back to server defaults when omitted.

**Architecture:** A domain `TranslationProfile` model represents validated provider + model combinations with badges and descriptions. `build_available_translation_profiles(settings)` discovers active profiles synchronously based on server readiness. `GET /api/capabilities` advertises available profiles with an `is_default` marker matching server configuration. `POST /api/jobs` accepts an optional `translation_profile_id`, which `JobService.create()` resolves to pin `job.translation_provider` and `job.translation_model` for consistent worker execution and chunk caching. `JobForm.tsx` renders a polished `StudioSelect` dropdown for profile selection.

**Tech Stack:** FastAPI, Pydantic v2, Python 3.12+ dataclasses, React 19, TypeScript, Vitest, Testing Library.

**Spec:** [docs/superpowers/specs/2026-10-26-translation-profile-selection-design.md](file:///home/teemo/documents/projects/text-to-audiobook/docs/superpowers/specs/2026-10-26-translation-profile-selection-design.md)

## Global Constraints

- **Language-Agnostic (ADR 0001):** Do not hard-code translation language pairs or source/target languages in business logic.
- **Provider Abstraction (ADR 0002):** Domain and route layers interact only through ports and profiles, not vendor-specific SDKs.
- **Async Jobs (ADR 0003):** Job creation returns `job_id` and `status: queued`; worker runs the pipeline using the pinned provider.
- **Cache Consistency (ADR 0006):** Pinned `translation_provider` and `translation_model` remain part of chunk artifact cache identity.
- **No Blocking Network Calls in Capabilities:** `CapabilitiesService.get()` must remain completely synchronous and configuration-driven without network probes.
- **Backward Compatibility:** Requests omitting `translation_profile_id` continue to resolve to the server's default translation pin without error.

## Review Focus

1. **Unknown Profile ID Rejection:** A client submits `translation_profile_id: "non-existent"` → Must fail fast with `DomainError(ErrorType.INVALID_INPUT)` and HTTP 400.
2. **Missing Gemini Key Omits Cloud Profiles:** Server boots without `GEMINI_API_KEY` → Capabilities response must not include Gemini profiles, preventing unexecutable options on UI.
3. **Test Isolation in Fake Mode:** `TRANSLATION_PROVIDER=fake` → Capabilities returns only `fake` profile with `is_default=True`, preserving test speed and isolation.
4. **Alignment of UI Default with JobService Fallback:** A request with no `translation_profile_id` must resolve to the identical `(provider, model)` marked with `is_default=True` in capabilities.
5. **Multipart & JSON Parity:** `translation_profile_id` must be extracted and applied identically whether submitted via JSON body or multipart form upload.

---

### Task 1: Domain Model, Capabilities Service & Factory Discovery

**Files:**
- Create: `backend/app/domain/translation_profile.py`
- Modify: `backend/app/application/capabilities.py:1-44`
- Modify: `backend/app/config/factory.py:120-200`
- Test: `backend/tests/config/test_factory.py`

**Interfaces:**
- Consumes: `Settings` from `app.config.settings`
- Produces:
  - `TranslationProfile` dataclass (`id`, `provider`, `model`, `label`, `badge`, `description`, `is_default`)
  - `build_available_translation_profiles(settings: Settings) -> list[TranslationProfile]`
  - `Capabilities.translation_profiles: list[TranslationProfile]`

- [ ] **Step 1: Write failing tests in `backend/tests/config/test_factory.py`**

```python
def test_build_available_translation_profiles_fake_mode() -> None:
    settings = Settings(_env_file=None, translation_provider="fake")
    profiles = build_available_translation_profiles(settings)
    assert len(profiles) == 1
    assert profiles[0].id == "fake"
    assert profiles[0].provider == "fake"
    assert profiles[0].is_default is True


def test_build_available_translation_profiles_with_gemini_and_nllb() -> None:
    settings = Settings(
        _env_file=None,
        translation_provider="nllb",
        gemini_api_key="valid-key",
        gemini_translation_model="gemini-3.5-flash",
        nllb_model_id="facebook/nllb-200-distilled-600M",
    )
    profiles = build_available_translation_profiles(settings)
    ids = [p.id for p in profiles]
    assert "gemini-3.5-flash" in ids
    assert "gemini-2.0-flash" in ids
    assert "nllb-600m" in ids
    nllb_profile = next(p for p in profiles if p.id == "nllb-600m")
    assert nllb_profile.is_default is True


def test_build_available_translation_profiles_omits_gemini_when_key_missing() -> None:
    settings = Settings(_env_file=None, translation_provider="nllb", gemini_api_key=None)
    profiles = build_available_translation_profiles(settings)
    ids = [p.id for p in profiles]
    assert not any("gemini" in pid for pid in ids)
    assert "nllb-600m" in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/bin/pytest backend/tests/config/test_factory.py -k "test_build_available_translation_profiles" -v`
Expected: FAIL (cannot import `build_available_translation_profiles`)

- [ ] **Step 3: Implement `TranslationProfile` and `build_available_translation_profiles`**

1. Create `backend/app/domain/translation_profile.py`:
```python
from dataclasses import dataclass


@dataclass(frozen=True)
class TranslationProfile:
    id: str
    provider: str
    model: str
    label: str
    badge: str
    description: str
    is_default: bool = False
```

2. Update `backend/app/application/capabilities.py`:
Add `translation_profiles: list[TranslationProfile] = field(default_factory=list)` to `Capabilities` dataclass.
In `CapabilitiesService.__init__`, accept `translation_profiles: list[TranslationProfile] | None = None`.
In `CapabilitiesService.get()`, pass `translation_profiles=self._translation_profiles`.

3. In `backend/app/config/factory.py`:
Implement `build_available_translation_profiles(settings: Settings) -> list[TranslationProfile]`:
- If `settings.translation_provider.strip().lower() == "fake"`:
  return `[TranslationProfile(id="fake", provider="fake", model="fake", label="Fake Translation (Mock)", badge="MOCK", description="Mock translation for testing", is_default=True)]`
- Else:
  Check `settings.gemini_api_key`. If present and non-empty, add `gemini-3.5-flash` (`label="Google Gemini 3.5 Flash"`, `badge="ONLINE"`, `description="Văn phong tự nhiên · Khuyên dùng"`) and `gemini-2.0-flash` (`label="Google Gemini 2.0 Flash"`, `badge="ONLINE"`, `description="Tốc độ cao · Tiết kiệm quota"`).
  Always add `nllb-600m` (`model=settings.nllb_model_id`, `label="NLLB-200 (CPU Offline)"`, `badge="OFFLINE"`, `description="Chạy hoàn toàn trên máy · Riêng tư"`).
  If `settings.translation_provider == "ollama"`: add `ollama-gemma` (`model=settings.ollama_translation_model`, `label="Ollama TranslateGemma"`, `badge="LOCAL"`, `description="Mô hình mã nguồn mở cục bộ"`).
  Match `default_provider, default_model = translation_pin_from_settings(settings)` to mark `is_default=True` on the corresponding profile.
Pass `build_available_translation_profiles(settings)` into `build_capabilities_service`.

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/bin/pytest backend/tests/config/test_factory.py -k "test_build_available_translation_profiles" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/domain/translation_profile.py backend/app/application/capabilities.py backend/app/config/factory.py backend/tests/config/test_factory.py
git commit -m "feat(domain,config): add translation profile domain model and discovery factory"
```

---

### Task 2: API Schemas, Capabilities Endpoint & JobService Profile Resolution

**Files:**
- Modify: `backend/app/api/schemas.py:70-95`
- Modify: `backend/app/api/capabilities.py:10-25`
- Modify: `backend/app/application/jobs.py:20-85`
- Modify: `backend/app/api/parse.py:50-105`
- Modify: `backend/app/config/factory.py:40-60`
- Test: `backend/tests/api/test_capabilities.py`
- Test: `backend/tests/application/test_create_job.py`
- Test: `backend/tests/api/test_jobs.py`

**Interfaces:**
- Consumes: `TranslationProfile` from `app.domain.translation_profile`
- Produces:
  - `TranslationProfileResponse` in `app.api.schemas`
  - `CapabilitiesResponse.translation_profiles`
  - `CreateJobJsonBody.translation_profile_id: str | None`
  - `CreateJobCommand.translation_profile_id: str | None`
  - `JobService.create()` resolves profile ID to pin `translation_provider` and `translation_model`

- [ ] **Step 1: Write failing tests in `backend/tests/application/test_create_job.py` and `backend/tests/api/test_capabilities.py`**

In `backend/tests/application/test_create_job.py`:
```python
def test_create_resolves_specified_translation_profile() -> None:
    from app.domain.translation_profile import TranslationProfile

    profiles = [
        TranslationProfile(
            id="gemini-flash",
            provider="gemini",
            model="gemini-3.5-flash",
            label="Gemini",
            badge="ONLINE",
            description="desc",
            is_default=False,
        ),
        TranslationProfile(
            id="nllb-600m",
            provider="nllb",
            model="facebook/nllb-200-distilled-600M",
            label="NLLB",
            badge="OFFLINE",
            description="desc",
            is_default=True,
        ),
    ]
    service, store, _, _ = _service(
        translation_provider="nllb",
        translation_model="facebook/nllb-200-distilled-600M",
        available_profiles=profiles,
    )
    cmd = CreateJobCommand(
        source_language="en-US",
        target_language="vi-VN",
        text="Hello story",
        translation_profile_id="gemini-flash",
    )
    job = asyncio.run(service.create(cmd))
    assert job.translation_provider == "gemini"
    assert job.translation_model == "gemini-3.5-flash"


def test_create_rejects_unknown_translation_profile() -> None:
    service, _, _, _ = _service()
    cmd = CreateJobCommand(
        source_language="en-US",
        target_language="vi-VN",
        text="Hello story",
        translation_profile_id="non-existent-profile",
    )
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(service.create(cmd))
    assert exc_info.value.error_type == ErrorType.INVALID_INPUT
```

In `backend/tests/api/test_capabilities.py`:
```python
def test_capabilities_returns_translation_profiles(client: TestClient) -> None:
    response = client.get("/api/capabilities")
    assert response.status_code == 200
    data = response.json()
    assert "translation_profiles" in data
    assert isinstance(data["translation_profiles"], list)
    assert len(data["translation_profiles"]) >= 1
    assert data["translation_profiles"][0]["id"] == "fake"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/bin/pytest backend/tests/application/test_create_job.py -k "test_create_resolves_specified_translation_profile" -v`
Expected: FAIL

- [ ] **Step 3: Implement API Schemas, Capabilities response, and JobService resolution**

1. In `backend/app/api/schemas.py`:
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
    translation_profiles: list[TranslationProfileResponse] = Field(default_factory=list)

class CreateJobJsonBody(BaseModel):
    text: str
    source_language: str
    target_language: str
    voice: str | None = None
    speed: float = SPEED_DEFAULT
    output_format: str = Field(default=OutputFormat.MP3.value)
    translation_profile_id: str | None = None
```

2. In `backend/app/api/capabilities.py`:
Serialize `result.translation_profiles` into `CapabilitiesResponse`.

3. In `backend/app/application/jobs.py`:
- Update `CreateJobCommand`: add `translation_profile_id: str | None = None`.
- In `JobService.__init__`: accept `available_profiles: Sequence[TranslationProfile] | None = None`.
- In `JobService.create(command: CreateJobCommand)`:
  If `command.translation_profile_id` is non-empty:
    Match profile by `p.id == command.translation_profile_id`.
    If not found: raise `DomainError(ErrorType.INVALID_INPUT, f"unknown translation_profile_id: {command.translation_profile_id}")`.
    Assign `job_provider = profile.provider`, `job_model = profile.model`.
  Else:
    Assign `job_provider = self._translation_provider`, `job_model = self._translation_model`.

4. In `backend/app/api/parse.py`:
Extract `translation_profile_id` in both `_parse_json` and `_parse_multipart`.

5. In `backend/app/config/factory.py`:
Pass `available_profiles=build_available_translation_profiles(settings)` into `build_job_service`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/bin/pytest backend/tests/application/test_create_job.py backend/tests/api/test_capabilities.py backend/tests/api/test_jobs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/schemas.py backend/app/api/capabilities.py backend/app/application/jobs.py backend/app/api/parse.py backend/app/config/factory.py backend/tests/api/test_capabilities.py backend/tests/application/test_create_job.py
git commit -m "feat(api,application): wire translation profile schema and job creation resolution"
```

---

### Task 3: Frontend Client, Types & JobForm StudioSelect UI Component

**Files:**
- Modify: `frontend/src/api/types.ts:1-50`
- Modify: `frontend/src/api/client.ts:50-97`
- Modify: `frontend/src/test/fixtures.ts:1-25`
- Modify: `frontend/src/components/JobForm.tsx:30-120,200-260`
- Test: `frontend/src/components/JobForm.test.tsx`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `CapabilitiesResponse.translation_profiles` from backend API
- Produces:
  - `TranslationProfile` TypeScript type
  - `CreateJobInput.translation_profile_id?: string`
  - UI dropdown in `JobForm.tsx` mapped from `capabilities.translation_profiles`

- [ ] **Step 1: Write failing tests in `frontend/src/components/JobForm.test.tsx`**

```tsx
it("renders translation profile select when capabilities include profiles", async () => {
  const user = userEvent.setup();
  const handleSubmit = vi.fn();
  const capabilitiesWithProfiles: Capabilities = {
    ...capabilitiesFixture,
    translation_profiles: [
      {
        id: "gemini-3.5-flash",
        provider: "gemini",
        model: "gemini-3.5-flash",
        label: "Google Gemini 3.5 Flash",
        badge: "ONLINE",
        description: "Văn phong tự nhiên · Khuyên dùng",
        is_default: true,
      },
      {
        id: "nllb-600m",
        provider: "nllb",
        model: "nllb-200-distilled-600M",
        label: "NLLB-200 (CPU Offline)",
        badge: "OFFLINE",
        description: "Chạy hoàn toàn trên máy · Riêng tư",
        is_default: false,
      },
    ],
  };

  render(
    <JobForm
      capabilities={capabilitiesWithProfiles}
      disabled={false}
      onSubmit={handleSubmit}
    />
  );

  const profileSelect = screen.getByLabelText("Translation engine");
  expect(profileSelect).toHaveValue("gemini-3.5-flash");

  // Type story and submit
  const textarea = screen.getByLabelText("Story text");
  await user.type(textarea, "A tale of stars.");
  const submitBtn = screen.getByRole("button", { name: /generate audiobook/i });
  await user.click(submitBtn);

  expect(handleSubmit).toHaveBeenCalledWith(
    expect.objectContaining({
      translation_profile_id: "gemini-3.5-flash",
    })
  );
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix frontend -- src/components/JobForm.test.tsx -t "renders translation profile select"`
Expected: FAIL (label "Translation engine" not found)

- [ ] **Step 3: Implement Frontend types, client, and JobForm UI**

1. In `frontend/src/api/types.ts`:
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
  source_language: string;
  target_language: string;
  voice?: string;
  speed: number;
  output_format: OutputFormat;
  text?: string;
  file?: File;
  translation_profile_id?: string;
};
```

2. In `frontend/src/api/client.ts`:
Forward `input.translation_profile_id` in JSON (`body.translation_profile_id`) and multipart (`form.set("translation_profile_id", ...)`).

3. In `frontend/src/test/fixtures.ts`:
Add mock `translation_profiles` to `capabilitiesFixture`.

4. In `frontend/src/components/JobForm.tsx`:
- Compute `defaultProfileId`: find profile with `is_default === true` or fallback to `profiles[0]?.id || ""`.
- State: `const [translationProfileId, setTranslationProfileId] = useState(defaultProfileId);`
- In `handleSubmit`: include `translation_profile_id: translationProfileId || undefined`.
- If `capabilities.translation_profiles && capabilities.translation_profiles.length > 0`:
  Render `StudioSelect` labeled `"Translation engine"` with options mapped from `translation_profiles`:
  `{ value: p.id, label: p.label, displayLabel: p.label, badge: p.badge, description: p.description }`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test --prefix frontend -- --run`
Expected: ALL PASS

- [ ] **Step 5: Run full backend and frontend test suites and linters**

Run:
```bash
backend/.venv/bin/pytest -m "not integration"
npm test --prefix frontend -- --run
npm run build --prefix frontend
backend/.venv/bin/ruff check backend --no-cache
backend/.venv/bin/ruff format --check backend --no-cache
backend/.venv/bin/python3 .github/scripts/scan_repo.py
```
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/api/client.ts frontend/src/test/fixtures.ts frontend/src/components/JobForm.tsx frontend/src/components/JobForm.test.tsx
git commit -m "feat(frontend): add translation profile selector to JobForm and client"
```
