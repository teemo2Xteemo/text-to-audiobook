# Data Model: Optional Local Literary Translation

## Translator choice (lab setting)

Not a stored entity. It is the API process’s current settings:

| Field | Source | Role |
| --- | --- | --- |
| Provider id | `TRANSLATION_PROVIDER` | `fake` (packaged default), `nllb` (free translator), or `ollama` (optional literary translator) |
| Model id | Matching setting | `fake`; `NLLB_MODEL_ID` when provider is `nllb`; `OLLAMA_TRANSLATION_MODEL` when provider is `ollama` |
| Address | `OLLAMA_BASE_URL` | Live location of a translator the operator runs. Not part of story identity |
| Per-request limit | `OLLAMA_HTTP_TIMEOUT_SECONDS` | Live. Not part of story identity |

Validation: provider id must be one of the three registered ids. Model id is non-empty. The base URL, when used, is an `http` or `https` URL. Unknown provider ids fail closed when a story is created or a pinned job is processed.

Relationships: capabilities and new stories read this choice. An existing story does not.

## Audiobook job

Existing job, plus the pin captured at creation.

| Field | Required for new jobs | Notes |
| --- | --- | --- |
| `id` | yes | Unchanged |
| `status` | yes | Unchanged state machine |
| `source_language`, `target_language` | yes | Per story. Not a translator choice |
| `voice`, `speed`, `output_format`, `output_bitrate_kbps` | yes | Unchanged |
| `chunk_current`, `chunk_total` | yes | Unchanged. Retry may reset counters; it does not clear the pin |
| `error_type`, `message` | no | Unchanged. Cleared on retry |
| `translation_provider` | yes for new jobs; absent on older records | Provider id at creation |
| `translation_model` | yes for new jobs; absent on older records | Model id at creation |

Validation:

- New jobs store both pin fields together. A create path that stores only one is invalid.
- The pin is copied from the lab setting inside the application service. It is not taken from the create request.
- Retry, failure marking, and stage updates preserve both fields.
- Older records omit both fields. Readers must accept that shape.

Relationships:

- One job has many story pieces (chunks), unchanged.
- The job’s pin selects which `TranslationProvider` instance the worker builds for every piece of that job.
- The job’s pin is the provider and model inside translation cache keys for that job.

State transitions: unchanged. `FAILED → QUEUED` remains only through retry. The pin does not change across that hop.

## Story piece

Unchanged stable chunk. A successful translation for a piece belongs to the job’s pin. A later job with the same text and a different pin is a different cache entry. Completing a piece still does not regenerate it when the pin is unchanged.

## Translation result

Unchanged cache entry shape. The key continues to include operation, text, source language, target language, provider, and model. For a pinned job, provider and model come from the job, not from the worker environment. Narration and speech settings stay out of the translation key.

## What is deliberately not stored

- No translator field on the create command or request body.
- No base URL or secret on the job.
- No second copy of the translation on the job status record; the result stays in the existing piece artifact and cache.
