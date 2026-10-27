# Multilingual story → audiobook

Web app that takes story text in a **user-selected source language** (or auto-detect) and produces narration audio in a **user-selected target language**.

Chinese → Vietnamese is a common example, not the only supported pair. Languages and voices come from `GET /api/capabilities`, not from a hard-coded list.

## Current status

- Product requirements: [`text-story-to-audiobook-requirements.md`](text-story-to-audiobook-requirements.md)
- AI coding rules: [`.cursor/rules/`](.cursor/rules/)
- Agent docs: [`docs/ai/`](docs/ai/)
- Implementation plan: [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md)
- Architecture decisions: [`docs/adr/`](docs/adr/)
- Feature specifications & plans: [`docs/superpowers/`](docs/superpowers/)

**MVP & Extended Capabilities in place:**
- **Core Pipeline (M1–M13):** FastAPI async jobs, Redis/RQ queue, domain ports, chunking with retry & checkpoint resume, translation/TTS caching, conservative narration, NLLB CPU adapter, Edge TTS + FFmpeg audio normalization, Docker Compose ([ADR 0010](docs/adr/0010-stack-and-project-layout.md)).
- **Translation Adapters:** NLLB (CPU distilled), Ollama/TranslateGemma ([ADR 0011](docs/adr/0011-ollama-translategemma-optional-translation.md)), Google Gemini API (`TRANSLATION_PROVIDER=gemini`), OpenRouter, Groq, and offline fakes. A clean clone still boots with `fake`. When `TRANSLATION_PROVIDER=openrouter`, the default profile is DeepSeek V4 Flash.
- **Narrate Mode & Pre-Submit Analysis:** Direct speech synthesis from text in source language without translation or LLM calls, script encoding and chapter analysis, fast candidate language detection, and voice audition previews.
- **High-Throughput Parallel Chunks:** Asynchronous parallel chunk processing with configurable concurrency limits for rapid translation and audio generation.
- **Audio & Subtitle Sync:** Timed SRT subtitle generation, automatic timestamp alignment, and synchronized timeline merging.
- **Studio UI & Hot Reload:** Modernized React Studio UI, translation profile selector, SRT timestamp cleaner, and hybrid host-Vite HMR dev mode.

### OpenRouter profiles

| Profile | Model | When it is the default |
| --- | --- | --- |
| `openrouter-deepseek-v4-flash` | `deepseek/deepseek-v4-flash` (paid) | `TRANSLATION_PROVIDER=openrouter` and `OPENROUTER_TRANSLATION_MODEL` is unset or set to this id |
| `openrouter-auto` | `openrouter/auto` | `OPENROUTER_TRANSLATION_MODEL=openrouter/auto` |

**Upgrade:** installs that used OpenRouter without setting `OPENROUTER_TRANSLATION_MODEL` previously defaulted to `openrouter/auto`. They now default to `deepseek/deepseek-v4-flash`. Jobs that already stored a translation pin keep that model. To keep auto-routing for new jobs, set `OPENROUTER_TRANSLATION_MODEL=openrouter/auto`.

## For coding agents

Start at [`AGENTS.md`](AGENTS.md) and [`docs/ai/README.md`](docs/ai/README.md). Do not hard-code languages or vendor SDKs into domain logic.

## Quick commands with Make

A `Makefile` at the repository root wraps setup, Compose, tests, and lint. GNU Make is a prerequisite. Install it with apt — there is no Make target for this, because that target could not run until Make is already installed:

```bash
sudo apt update && sudo apt install -y make
```

```bash
make help          # Show all available make targets
make setup         # Create .env and install backend/frontend deps into backend/.venv
make up            # Start all Docker services in background
make dev-hybrid    # Rebuild backend images with the dev source mount, then run Vite on the host
make dev-hybrid-build  # Deprecated alias of dev-hybrid
make test          # Run all unit tests (backend pytest + frontend vitest)
make lint          # Run ruff check/format and TypeScript typecheck
make check         # Run full pre-commit verification (lint + test + security/policy scans)
make down          # Stop all containers
make clean         # Remove host-owned caches; prints a Docker command if root owns them
```

## Run

Copy the example env file, then start all four services (frontend, api, worker, redis). CPU only — Compose has no GPU runtime.

```bash
cp .env.example .env
docker compose up --build
```

| Service | URL |
| --- | --- |
| Job UI | http://127.0.0.1:8080 |
| API health | http://127.0.0.1:8000/health → `{"status":"ok","service":"api"}` |
| Redis | 127.0.0.1:6379 |

The **worker** image installs FFmpeg via apt (the slim API image does not). FFmpeg is invoked with argv lists only; no Python ffmpeg binding. API and worker start as root only long enough to `chown` the `./storage` bind-mount (a **real directory**, not a symlink; `chown` walks the tree on every start and can get slower as `storage/` grows), then drop to uid 1000 (`app`). Frontend nginx listens on container port 80, published as `127.0.0.1:8080`.

Default providers are `fake` / `fake` so a clean clone boots **offline**. `VITE_*` values are baked into the frontend image; after changing them, run `docker compose up --build` again.

### Offline smoke (fakes)

1. `cp .env.example .env` and `docker compose up --build` (leave `TRANSLATION_PROVIDER=fake`, `TTS_PROVIDER=fake`).
2. Open http://127.0.0.1:8080 and confirm `GET /health` on port 8000.

### Acceptance demo (NLLB + Edge)

Needs network on first worker start, CPU, and about 16 GB RAM. Distilled NLLB weights download into the Compose `huggingface-cache` volume (`HF_HOME=/data/huggingface` in the worker — not a Settings field). Never commit those weights. Edge TTS also needs network. Do not set `NLLB_MODEL_ID` to 3.3B.

1. In `.env` set `TRANSLATION_PROVIDER=nllb` and `TTS_PROVIDER=edge`.
2. `docker compose up --build`.
3. In the UI, **upload** [`backend/tests/fixtures/sample-zh-CN.txt`](backend/tests/fixtures/sample-zh-CN.txt) (or paste the same text). Select source **zh-CN** (or Auto) and target **vi-VN** from the capabilities dropdowns, then a matching voice. Generate and wait until the job is `completed`; play the MP3.
4. Second pair (same UI, still from capabilities — not a hard-coded pair): upload or paste [`backend/tests/fixtures/sample-en-US.txt`](backend/tests/fixtures/sample-en-US.txt), select source **en-US** and target **vi-VN**, generate, and play.

### Optional literary translation (Ollama + TranslateGemma)

NLLB stays the Compose/MVP default. For local LLM translation ([ADR 0011](docs/adr/0011-ollama-translategemma-optional-translation.md)), run Ollama **on the host** (do not co-load NLLB in the same worker):

```bash
ollama pull translategemma:4b
```

In `.env`, set only:

```bash
TRANSLATION_PROVIDER=ollama
```

Compose maps `host.docker.internal` into **api** and **worker** (`extra_hosts`) and defaults `OLLAMA_BASE_URL` to `http://host.docker.internal:11434`. Restart **api and worker** after changing the provider (`GET /api/capabilities` is built from the translation provider). Override `OLLAMA_BASE_URL` only if Ollama is not on the host (LAN IP). A host-run worker (no Compose) still uses Settings default `http://127.0.0.1:11434`. Leave `TTS_PROVIDER=edge` or `fake`. Raise `RQ_JOB_TIMEOUT_SECONDS` if long chapters plus a slow CPU model exceed the worker soft timeout (`OLLAMA_HTTP_TIMEOUT_SECONDS` is per HTTP call, not the RQ limit).

There is **no Retry or Cancel button** in the SPA. To retry a `failed` job (same `job_id`, keeps checkpoints):

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/jobs/${JOB_ID}/retry"
```

GitHub Actions (`.github/workflows/ci.yml`) runs unit tests, linters, and a secret/policy scan on `main` and pull requests. It skips `@pytest.mark.integration` (real FFmpeg) and does not run Compose E2E.

Frontend unit tests and typecheck (Node 22):

```bash
cd frontend
npm ci
npm test
npm run typecheck
```

### Local UI development with Hot Reload (Hybrid Mode)

When actively developing UI components and styling, running the frontend inside the standard Docker Compose stack (`docker compose up`) serves pre-built static assets via Nginx on port 8080, which requires rebuilding the Docker image on every change.

To get instant **Hot Module Replacement (HMR)** with sub-second feedback, run the backend services in Docker and Vite dev server directly on the host:

0. **Ensure root environment file exists:**
   ```bash
   cp -n .env.example .env
   ```
   Vite is configured (`envDir: ".."`) to read `VITE_*` variables directly from the repository root `.env`. Keep `VITE_API_BASE_URL` empty (the default) so requests route through the Vite proxy rather than triggering direct CORS requests.

1. **Start backend services in Docker:**
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build redis api worker
   ```
   This rebuilds the api and worker images, so dependency changes in the image are installed, and boots Redis, FastAPI (published on `127.0.0.1:8000`), and the RQ worker. The dev overlay bind-mounts `./backend/app` into those containers and starts uvicorn with `--reload`. `make dev-hybrid` runs this same Compose command, then `npm run dev` in `frontend/`. `make dev-hybrid-build` is a deprecated alias that prints a note and runs `dev-hybrid`.

   `make up` and `docker compose up` use `docker-compose.yml` alone and run the code baked into the image. Rebuild the dev stack when `pyproject.toml` or the Dockerfiles change. The RQ worker does not auto-reload; restart the worker container after worker code changes.

2. **Start Vite development server on the host:**
   ```bash
   cd frontend
   npm ci
   npm run dev
   ```
   Open **http://127.0.0.1:5173** in your browser (`host: "127.0.0.1"` and `strictPort: true` ensure consistent IPv4 binding on port 5173).

3. **Instant HMR & Proxying Benefits:**
   - Edits to React components in `frontend/src/` reflect immediately via Vite HMR without a full page reload (root config or HTML edits trigger a standard reload).
   - Vite proxies `/api` and `/health` directly to `http://127.0.0.1:8000`. Upstream `Host` header is rewritten with `changeOrigin: true`. Audio responses and Range/206 seek requests are streamed natively by Vite's proxy without buffering.
   - Connecting directly to `127.0.0.1:8000` on the host avoids the Docker Nginx stale-DNS 502 error described in [Troubleshooting](#frontend-api-returns-502-after-recreating-api) (though temporary connection errors will appear while `api` is actively restarting).

4. **Teardown backend services when done:**
   ```bash
   docker compose down
   ```

Backend unit tests (Redis not required). From the **repo root**, `pytest.ini` registers the `integration` marker:

```bash
backend/.venv/bin/pytest -m "not integration"
```

Or from `backend/` (same marker, via `pyproject.toml`):

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -m "not integration"
```

## API Reference

The FastAPI backend exposes endpoints for capabilities discovery, pre-submit script analysis and voice auditions, asynchronous job lifecycle management, and audio/subtitle downloads.

### Capabilities (`GET /api/capabilities`)

Returns supported languages, available voices, active translation profiles, and duration estimation metrics.

```bash
curl -sS http://127.0.0.1:8000/api/capabilities
```

**Fields:**
- `languages`: Languages supported for translation (intersection of translation provider and TTS voices).
- `narrate_languages`: Languages directly supported by the TTS provider for narration without translation.
- `voices`: Available voices with `id`, `language`, and `label`. Filterable via query parameter: `?language=<bcp47>`.
- `translation_profiles`: Configured translation profiles (`id`, `provider`, `model`, `label`, `badge`, `description`, `is_default`, `supported_languages`).
- `estimate_defaults`: Default duration calculation parameters (`chars_per_second`, `preview_wpm`).

---

### Pre-Submit Analysis & Auditions

Clients can inspect file encoding, language candidates, chapter splits, and audition voice snippets before creating a job.

#### 1. Language Detection (`POST /api/detect-language`)

Fast candidate language detection on a sample (evaluates up to the first 8 KB) with confidence scores and preview lines. Accepts JSON (`{"text": "..."}`) or multipart form data.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/detect-language \
  -H "Content-Type: application/json" \
  -d '{"text": "Once upon a time in a faraway kingdom..."}'
```

**Response:**
```json
{
  "language": "en-US",
  "confidence": 0.99,
  "needs_confirmation": false,
  "candidates": [
    {"language": "en-US", "confidence": 0.99}
  ],
  "preview_lines": ["Once upon a time in a faraway kingdom..."]
}
```
*Note:* If top candidate `confidence` falls below `LANGUAGE_CONFIRM_THRESHOLD` (default `0.70`), `needs_confirmation` is set to `true`.

#### 2. Pre-Submit Script Analysis (`POST /api/analyze`)

Comprehensive analysis for full text or uploaded files (`.txt`). Detects file encoding (UTF-8, GB18030, Shift-JIS, etc.), ranked language candidates, chapter breakdown, and estimated audio and processing times. Accepts JSON or multipart form data.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/analyze \
  -F "file=@story.txt"
```

**Response:**
```json
{
  "encoding": "utf-8",
  "language": "zh-CN",
  "confidence": 0.98,
  "needs_confirmation": false,
  "candidates": [
    {"language": "zh-CN", "confidence": 0.98}
  ],
  "chapters_count": 10,
  "chapters_preview": [
    {"index": 1, "title": "第一章 少年", "char_count": 2840}
  ],
  "duration_estimate_seconds": 189.3,
  "process_time_estimate_seconds": 28.4,
  "preview_lines": ["第一章 少年", "..."]
}
```

#### 3. Voice Audition Preview (`POST /api/tts/preview`)

Generates a short audition snippet (~10 seconds, maximum 300 characters) for quick voice and speed evaluation before submitting a job.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/tts/preview \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello, this is an audio audition preview.",
    "voice": "en-US-JennyNeural",
    "speed": 1.0
  }'
```

**Response:**
```json
{
  "audio_base64": "<base64-encoded-audio>",
  "mime_type": "audio/mpeg"
}
```

---

### Job Lifecycle

#### 1. Create Job (`POST /api/jobs`)

Creates an asynchronous generation job. Accepts JSON (`CreateJobJsonBody`) or multipart form data (`file` or `text`). Returns HTTP 202 with `job_id` and initial `status: "queued"`.

| Field | Type | Default | Description |
|---|---|---|---|
| `text` / `file` | string / file | *Required* | Exactly one of plain text string or uploaded file (`.txt` or `.srt`). |
| `mode` | string | `"translate"` | `"translate"` (translate then narrate) or `"narrate"` (direct narration in source language, zero model cost). |
| `source_language` | string | `"auto"` | BCP-47 language tag or `"auto"` (auto-detected via language detector). |
| `target_language` | string | `""` | Target language tag. Required in `translate` mode. In `narrate` mode, optional (defaults to resolved source language). |
| `voice` | string | `null` | Voice ID. If omitted, resolved via language-family fallback matching the language. |
| `speed` | float | `1.0` | Speech rate multiplier (`0.5` to `2.0`). |
| `output_format` | string | `"mp3"` | Audio output container: `"mp3"` or `"wav"`. |
| `translation_profile_id` | string | `null` | Optional translation profile from capabilities. Allowed in `translate` mode; rejected in `narrate` mode. |

**Narrate Mode Example (Direct Narration Without Translation):**
```bash
curl -sS -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "text": "This story is narrated directly in the original language without translation...",
    "mode": "narrate",
    "source_language": "en-US",
    "output_format": "mp3"
  }'
```

**Translate Mode Example:**
```bash
curl -sS -X POST http://127.0.0.1:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Once upon a time in a faraway kingdom...",
    "mode": "translate",
    "source_language": "en-US",
    "target_language": "vi-VN",
    "output_format": "mp3"
  }'
```
*Note:* In `translate` mode, if `source_language == target_language`, translation is automatically bypassed using pass-through checkpoints.

#### 2. Get Job Status (`GET /api/jobs/{job_id}`)

Polls current progress, stage, chunk counters, error details, and artifact download links.

```bash
curl -sS http://127.0.0.1:8000/api/jobs/${JOB_ID}
```

**Response:**
```json
{
  "job_id": "c9a646d3-9c61-4cc9-bc07-7b833ef1cbbe",
  "status": "completed",
  "stage": "completed",
  "mode": "narrate",
  "chunk_current": 4,
  "chunk_total": 4,
  "source_language": "vi-VN",
  "target_language": "vi-VN",
  "voice": "vi-VN-HoaiMyNeural",
  "speed": 1.0,
  "output_format": "mp3",
  "audio_url": "/api/jobs/c9a646d3-9c61-4cc9-bc07-7b833ef1cbbe/audio",
  "subtitles_url": "/api/jobs/c9a646d3-9c61-4cc9-bc07-7b833ef1cbbe/subtitles",
  "error_type": null,
  "message": null
}
```

#### 3. Download Artifacts

- **Audio:** `GET /api/jobs/{job_id}/audio` — streams the generated audiobook (`audio/mpeg` or `audio/wav`) with HTTP Range (206) seek support.
- **Subtitles:** `GET /api/jobs/{job_id}/subtitles` — downloads timed SubRip subtitles (`.srt`) aligned with narration timestamps.

#### 4. Retry Failed Job (`POST /api/jobs/{job_id}/retry`)

Retries a job in `failed` status from its last valid checkpoint without repeating completed chunks. Returns HTTP 202.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/jobs/${JOB_ID}/retry
```

## Troubleshooting

### Frontend `/api` returns 502 after recreating `api`

Nginx in the `frontend` container resolves the Compose hostname `api` at process start and can cache a stale container IP. Recreating `api` (or `worker`) without recreating `frontend` then returns **502** for `/api` and `/health` on port 8080, even when `http://127.0.0.1:8000/health` is fine.

Recreate frontend after those services:

```bash
docker compose up -d --force-recreate frontend
```

### Job stays `translating` / `generating_audio` after the worker dies

RQ **soft timeout** (`JobTimeoutException` / `RQ_JOB_TIMEOUT_SECONDS`) runs `on_failure` and marks the job `failed` with `error_type=TIMEOUT`. The SPA can show that error; retry with `POST /api/jobs/{job_id}/retry`.

**Hard kill is not the same path.** OOM, `SIGKILL`, or an RQ work-horse death penalty that never raises in-process will **not** run those hooks. `status.json` can stay non-terminal; worker boot recover will re-enqueue (M11). There is no heartbeat / “stale in-progress → failed” sweeper yet.

### `STORAGE_PATH` must be a real directory

Do not point `STORAGE_PATH` at a symlink. The entrypoint only `lchown`s a symlink itself (it does not follow the target, so contents may stay root-owned and uid 1000 cannot write). Compose’s `./storage` bind-mount is a real directory.

### Ollama translation (`TRANSLATION_PROVIDER=ollama`)

**Ollama is not running / connection refused.** Compose defaults `OLLAMA_BASE_URL` to `http://host.docker.internal:11434` and adds `extra_hosts` so that hostname is the **host**, not the container. Confirm `ollama serve` is listening on the host. A host-run process (no Compose) uses `http://127.0.0.1:11434`. Jobs should fail with typed `TRANSLATION_FAILED` or `TIMEOUT`, not stay non-terminal.

**Model not pulled.** `ollama pull translategemma:4b` (or `:12b`). A missing tag typically returns HTTP 4xx/5xx mapped to `TRANSLATION_FAILED`.

**Timeout under load.** `OLLAMA_HTTP_TIMEOUT_SECONDS` is the per-request HTTP timeout (default 120). RQ still kills the worker function at `RQ_JOB_TIMEOUT_SECONDS`. Raise the RQ value for long chapters on CPU; raising only the HTTP timeout does not keep the job alive past RQ.

**Do not co-load NLLB.** When `TRANSLATION_PROVIDER=ollama`, the factory does not construct the NLLB engine. Do not also set the worker to load NLLB weights in the same process.

### `make clean` and root-owned caches

`make clean` deletes host-owned `__pycache__`, pytest, and Ruff caches. Docker can leave `.pytest_cache` and `.ruff_cache` owned by root or `nobody`. When `rm` cannot delete those paths, `make clean` exits non-zero and prints:

```bash
docker run --rm -v "$PWD:/work" -w /work alpine rm -rf .pytest_cache backend/.pytest_cache .ruff_cache backend/.ruff_cache .coverage htmlcov
```

Host `make lint-backend` already keeps the Ruff cache under `/tmp` (`RUFF_CACHE_DIR`). Root-owned `__pycache__` directories are skipped rather than failing the target; remove them with the same `docker run` command if they remain.

### Translation profiles & Worker environment

When running API and worker processes in separate containers, all credentials or backends behind advertised profiles (such as `GEMINI_API_KEY` for Google Gemini or `OLLAMA_BASE_URL` for Ollama) must be configured in the **worker environment** as well. The web API discovers available profiles from its own environment, but background translation jobs execute inside the worker process. If a worker lacks the required configuration (e.g. missing `GEMINI_API_KEY`), the job immediately transitions to terminal `FAILED` status with `error_type: PROVIDER_CONFIGURATION_ERROR`.
