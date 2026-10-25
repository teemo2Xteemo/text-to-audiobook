# Contract: Job status pin

Additive fields on the existing job status resource. Job states and transitions are unchanged.

## `GET /api/jobs/{job_id}`

Existing fields stay. New jobs also return:

| Field | Type | Meaning |
| --- | --- | --- |
| `translation_provider` | string | Provider id captured at create (`fake`, `nllb`, or `ollama`) |
| `translation_model` | string | Model id captured at create |

Example fragment:

```json
{
  "job_id": "…",
  "status": "queued",
  "translation_provider": "ollama",
  "translation_model": "translategemma:4b"
}
```

Rules:

- Both fields are present for every job created after this change, in every status including `failed` and `completed`.
- `POST /api/jobs/{job_id}/retry` returns the same pair. Retry does not replace them with the current lab choice.
- Jobs stored before this change may omit both fields. Clients treat a missing pair as “this record has no pin,” not as an error.
- The create response does not gain these fields.

## On-disk `status.json`

The same two keys are persisted with the job. Updates to status, chunk counters, and errors must round-trip the pin. Readers accept files that lack both keys. A file with only one of the two keys is invalid for new writes; readers should treat a half-written pair as a missing pin rather than invent the other half.

## Worker

When both fields are present, that job’s translation adapter and translation cache identity use them, even if the worker process environment names a different provider or model. When both are absent, the worker uses the current environment, matching older records.

The worker still does not translate inside the HTTP request.
