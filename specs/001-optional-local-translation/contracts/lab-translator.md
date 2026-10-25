# Contract: Lab translator choice

The lab choice is operator configuration, not a field on story submission.

## Create story

`POST /api/jobs` (JSON or upload) stays limited to the story, languages, voice, speed, and output format.

| Rule | Meaning |
| --- | --- |
| No translator input | A `translation_provider` or model field on the request is not part of this contract. Clients must not send one. The server does not select a translator from the body. |
| Choice in effect | The stored pin is the API process’s `TRANSLATION_PROVIDER` and the matching model id at the moment the job is saved. |
| Response | `202` body remains `{ "job_id", "status" }`. |

Switching translators means changing the environment and restarting the API so later creates and `GET /api/capabilities` follow the new choice. Stories already stored keep the pin from their create.

## Capabilities

`GET /api/capabilities` continues to list languages and voices for the lab choice currently loaded in the API process. It does not describe the pin of an older story.

## Reachability

When the pin is `ollama`, the worker calls the live `OLLAMA_BASE_URL`. That URL is where the operator runs the translator (this computer or another they control). This feature does not add a paid or public translation service. Connection failures, missing models, timeouts, unsupported languages, empty results, and over-long pieces surface as the existing typed job errors. Logs omit story text and credentials.

## Defaults

| Setting | Packaged value | Effect |
| --- | --- | --- |
| `TRANSLATION_PROVIDER` | `fake` | New stories do not require the literary model |
| `OLLAMA_TRANSLATION_MODEL` | `translategemma:4b` | Used only when the provider is `ollama` |
| `NLLB_MODEL_ID` | existing free-translator default | Used only when the provider is `nllb` |

Selecting `ollama` must not construct the NLLB adapter for that job, and the reverse.
