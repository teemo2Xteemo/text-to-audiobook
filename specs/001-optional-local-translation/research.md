# Research: Optional Local Literary Translation

## 1. What is already implemented

**Decision:** Treat the Ollama + TranslateGemma adapter, lab settings, and live-settings cache identity as existing behavior (ADR 0011). This plan does not add a second adapter or change the packaged default.

**Rationale:** `TRANSLATION_PROVIDER=ollama` already builds `OllamaTranslationProvider` from `OLLAMA_BASE_URL`, `OLLAMA_TRANSLATION_MODEL` (default `translategemma:4b`), and `OLLAMA_HTTP_TIMEOUT_SECONDS`. Cache keys already include provider and model when taken from the current settings. The default remains `fake`. NLLB stays the free translator when that provider is selected. Narration stays a separate stage. Unit tests use a fake HTTP client.

**Alternatives considered:** Rebuilding the adapter in this feature. Rejected because the quality path (full-text guard, commentary stripping, language mapping inside the adapter, typed failures) is already there. A paid hosted adapter. Rejected; it stays a later feature.

## 2. Where a story gets its translator

**Decision:** One lab-wide choice, captured from the API process settings when the story is created. The create request does not accept a translator or model. `GET /api/capabilities` keeps describing the lab choice currently loaded in the API process.

**Rationale:** The spec requires one choice for every new story and forbids a per-story submission control. Settings are already process-wide (`TRANSLATION_PROVIDER` plus the matching model id). Restarting the API after a settings change is how the next story picks up the new choice, which matches the rest of this app’s configuration.

**Alternatives considered:** A translator field on `POST /api/jobs`. Rejected by the spec. Reading the worker’s environment again for each piece. Rejected because that moves an unfinished story onto a later lab choice.

## 3. Unfinished stories keep their translator

**Decision:** Persist `translation_provider` and `translation_model` on the job record at creation. The worker and the translation cache for that job use those values, including resume, boot recovery, and `POST /api/jobs/{id}/retry`. A later change to `TRANSLATION_PROVIDER` or the model id applies only to jobs created after the API is restarted with the new settings.

**Rationale:** Today `process_job` calls `build_orchestrator(Settings())`, so cache identity and the adapter follow the worker’s current environment. That contradicts the spec: an unfinished story must keep the translator and model it started with, and a new story must not be given the earlier story’s stored translation. `retry` already keeps the same job id and uses `replace` for status fields only, so new identity fields survive a retry if they are not cleared there.

**Alternatives considered:** Re-translate an unfinished story when the lab choice changes. Rejected. Discard the unfinished story. Rejected. Snapshot only in memory. Rejected because the worker is a separate process and must still see the pin after a restart.

## 4. What is pinned vs what stays live

**Decision:** Pin the provider id (`fake`, `nllb`, or `ollama`) and the model id that belongs to that provider (`fake`, `NLLB_MODEL_ID`, or `OLLAMA_TRANSLATION_MODEL`). Keep `OLLAMA_BASE_URL`, HTTP timeout, and queue timeout as live operator configuration.

**Rationale:** The spec’s identity is the translator and model, so those belong on the job and in the cache key. The address is how the operator reaches a translator they run, including another computer they control. Moving that address must not mint a new translation identity. Timeouts are operational limits, not a different translation.

**Alternatives considered:** Pin the base URL on the job. Rejected because an operator who moves the translator would be stuck with a dead address for every unfinished story. Leave model id only in the environment. Rejected because two models would share or steal cache entries across stories.

## 5. Memory

**Decision:** For each job run, construct only the translator named on that job. Do not also construct the other heavy translator in that run. Leave worker concurrency at its current default of 1.

**Rationale:** The spec forbids holding the free heavy translator and the literary translator at the same time for one job. The factory already branches so `ollama` does not construct NLLB and the reverse. Per-job construction must keep that exclusive branch. Sequential jobs with different pins can still load one heavy model after the other; that is an operator concern on a ~16 GB lab machine, not a reason to mix two models inside one story.

**Alternatives considered:** Refuse to run a pinned job whose provider differs from the current environment. Rejected because the unfinished story would then follow the new lab choice or stall. Add a second worker. Rejected as extra surface; concurrency stays configurable and defaults to 1.

## 6. Older job records

**Decision:** Job records written before this change have no pin fields. The worker treats a missing pin as “use the current lab settings,” which is today’s behavior. Jobs created after this change always store both fields. Loading an old record must not fail.

**Rationale:** Existing `status.json` files must stay readable. The spec’s pin applies to stories created once the behavior exists. Rewriting old files to stamp the current settings would pretend those stories had been pinned earlier.

**Alternatives considered:** Migration that writes the current settings onto every old job. Rejected because a settings change between migration and the story’s original run would label the story with the wrong translator.

## 7. Empty input

**Decision:** No new empty-piece rule. Story creation already rejects blank text. Whitespace-only chunks stay on the existing chunker.

**Rationale:** The spec lists the case and leaves the outcome to current piece handling. A new failure type would change product behavior outside the literary-translator scope.

## 8. Literary quality checks

**Decision:** Automated tests cover the pin, cache separation, exclusive adapter construction, and the adapter’s existing guards (no silent shortening, translation-only text, typed failures) with a fake HTTP client. A live model comparison of the documented sample stays an optional manual check, not a CI gate.

**Rationale:** Constitution tests must not download models or require a running translator. “Close enough to narrate” is a reviewer judgment on the sample, not an automated score. The adapter must already refuse input over its character guard instead of returning a shortened success.

**Alternatives considered:** A similarity score against a hosted-assistant reference in CI. Rejected because the reference is not a service this product calls, and the score would not match the reviewer-based success criterion.
