# Quickstart: Optional Local Literary Translation

Validation for the pin behavior. Adapter quality guards already have unit tests under `backend/tests/providers/test_ollama_translation.py`. This guide does not replace those and does not require a downloaded model.

## Prerequisites

- Backend test extras installed (`pytest`, the project in the environment you already use for `backend/`).
- No Redis, model download, or running translator for the automated path.

## Automated

From `backend/`:

```bash
pytest -m "not integration" tests/config/test_factory.py tests/providers/test_ollama_translation.py tests/application/test_pipeline.py tests/workers/test_process_job.py
```

Add the new job-pin tests in the same run once they exist (job create, status round-trip, worker, retry). Expected:

- Create with lab provider `ollama` and model `translategemma:4b` stores that pair and does not read a translator from the request body.
- Create with lab provider `fake` stores `fake` and does not require the literary model.
- A second create after the lab provider moves to `nllb` stores `nllb` and that model id. The first job’s stored pair is unchanged.
- Processing or retrying the first job still resolves the translation adapter and cache identity from the first job’s pair.
- Building the adapter for an `ollama` pin does not construct the NLLB adapter, and the reverse.
- A status file without the two fields still loads, and the worker uses current settings for that job.
- Failed-piece retry keeps the original pair. Changing settings does not replace it.

Default offline suite remains:

```bash
pytest -m "not integration"
```

## Manual literary sample (optional)

Only when a local translator is already running and the model is installed:

1. Set `TRANSLATION_PROVIDER=ollama` and `OLLAMA_TRANSLATION_MODEL=translategemma:4b` (or `translategemma:12b` when that is the only heavy translator loaded). Point `OLLAMA_BASE_URL` at the translator you run.
2. Restart the API so new stories and capabilities follow that choice. Submit the documented sample story with its source and target languages. Do not put a translator name in the request.
3. Confirm status shows the pin you started with, the translation covers the whole sample, and names, the medical term, and the idiom are narratable against the literary reference.
4. While that job is unfinished, change `TRANSLATION_PROVIDER` or the model and restart only the API. Confirm the unfinished job’s status pair does not change, and a new story shows the new pair.

Do not point this check at a paid or public translation service.
