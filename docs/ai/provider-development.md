# Provider development

Adapters implement domain ports. Orchestration never imports vendor SDKs.

## Ports (conceptual)

Vendor adapters implement `TranslationProvider` and `TTSProvider` under `backend/app/providers/`. Exact types live in `backend/app/domain` — do not copy a second interface into the worker, application, or a later milestone.

```python
class TranslationProvider(Protocol):
    async def translate(self, text: str, source_language: str, target_language: str) -> str: ...
    def supported_languages(self) -> Sequence[str]: ...

class TTSProvider(Protocol):
    async def synthesize(self, text: str, language: str, voice: str, settings: TTSSettings) -> AudioArtifact: ...
    def voices_for(self, language: str) -> Sequence[Voice]: ...
```

Pipeline-stage and detection ports also live in `backend/app/domain`. They are **not** a third vendor family under `providers/translation` or `providers/tts` (ADR 0005).

```python
class NarrationProcessor(Protocol):
    def process(self, text: str, language: str) -> str: ...

class LanguageDetector(Protocol):
    async def detect(self, text: str) -> LanguageDetection: ...  # language_code + confidence
```

M7 implements `NarrationProcessor`. M8 adds a CPU `LanguageDetector` adapter at `backend/app/providers/language_detection/` (not under `translation/` or `tts/`). BCP-47 / `auto` → vendor codes still stay inside translation/TTS adapters. Low-confidence threshold is adapter/config, not a domain constant.

## Adding a translation adapter

1. Implement the port under `backend/app/providers/translation/`.
2. Map **domain** language tags (BCP-47, plus `auto` for source) to provider codes **inside the adapter**.
3. Surface unsupported pairs as `UNSUPPORTED_LANGUAGE`, not a generic 500.
4. Register the adapter in config (`TRANSLATION_PROVIDER=...`) / DI — no `if provider ==` in application services.
5. Fake it in unit tests; optional integration test behind a marker.

MVP candidate: NLLB (CPU distilled). Later: LibreTranslate, OpenAI, Gemini.

## Adding a TTS adapter

1. Implement the port under `backend/app/providers/tts/`.
2. Filter voices by `target_language`. Never expose a voice that cannot speak that language.
3. Normalize output through the FFmpeg infrastructure helper before merge (codec/rate/channels will differ by vendor).
4. Edge TTS is the first adapter, not a special case in domain code.

Later: Piper, XTTS, Chatterbox, ElevenLabs, Azure, Google.

## Language tags

- **Domain:** BCP-47 (`zh-CN`, `vi-VN`, `ja-JP`, `en-US`) and `auto` for source.
- **Provider:** whatever the SDK wants (`zho_Hans`, `vi-VN-NamMinhNeural`). Mapping stays in the adapter.

## Checklist

- [ ] No vendor import outside `providers/` (and tests/fakes)
- [ ] Cache key uses provider + model + languages + voice + settings
- [ ] Failures are typed (`TRANSLATION_FAILED`, `TTS_FAILED`, `PROVIDER_RATE_LIMIT`)
- [ ] Logs include `job_id`, `chunk_id`, provider — not API keys or full text
