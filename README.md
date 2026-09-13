# Multilingual story → audiobook

Web app that takes story text in a **user-selected source language** (or auto-detect) and produces narration audio in a **user-selected target language**.

Chinese → Vietnamese is a common example, not the only supported pair. Languages and voices come from `GET /api/capabilities`, not from a hard-coded list.

## Current status

- Product requirements: [`text-story-to-audiobook-requirements.md`](text-story-to-audiobook-requirements.md)
- AI coding rules: [`.cursor/rules/`](.cursor/rules/)
- Agent docs: [`docs/ai/`](docs/ai/)
- Implementation plan: [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md)
- Architecture decisions: [`docs/adr/`](docs/adr/)

**M1**–**M13** are in place (API + Redis Compose, domain contracts, job HTTP/enqueue, pipeline orchestrator, RQ worker + DI + capabilities, Vite job UI, conservative narration, NLLB CPU adapter, Edge TTS + FFmpeg normalize, chunk retry, checkpoint resume, translation/TTS cache, Compose completeness / MVP acceptance). Stack: React + TypeScript, FastAPI, Redis/RQ, NLLB (CPU), Edge TTS, FFmpeg, Docker Compose ([ADR 0010](docs/adr/0010-stack-and-project-layout.md)).

## For coding agents

Start at [`AGENTS.md`](AGENTS.md) and [`docs/ai/README.md`](docs/ai/README.md). Do not hard-code languages or vendor SDKs into domain logic.

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

In `.env`:

```bash
TRANSLATION_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_TRANSLATION_MODEL=translategemma:4b
OLLAMA_HTTP_TIMEOUT_SECONDS=120
```

Restart **api and worker** after changing these (`GET /api/capabilities` is built from the translation provider). On Linux/WSL, if `host.docker.internal` does not resolve from the container, use `http://172.17.0.1:11434` or the host LAN IP. You can also set Compose `extra_hosts: ["host.docker.internal:host-gateway"]` on api/worker; it is optional. Leave `TTS_PROVIDER=edge` or `fake`. Raise `RQ_JOB_TIMEOUT_SECONDS` if long chapters plus a slow CPU model exceed the worker soft timeout (`OLLAMA_HTTP_TIMEOUT_SECONDS` is per HTTP call, not the RQ limit).

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

Local Vite against a running API (`npm run dev` on `http://127.0.0.1:5173`, proxies `/api` and `/health`):

```bash
cd frontend
npm ci
npm run dev
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

**Ollama is not running / connection refused.** The adapter talks HTTP to `OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`). From Compose, `127.0.0.1` is the **container**, not the host. Point at the host (`host.docker.internal`, `172.17.0.1`, or the host LAN IP) and confirm `ollama serve` is listening. Jobs should fail with typed `TRANSLATION_FAILED` or `TIMEOUT`, not stay non-terminal.

**Model not pulled.** `ollama pull translategemma:4b` (or `:12b`). A missing tag typically returns HTTP 4xx/5xx mapped to `TRANSLATION_FAILED`.

**Timeout under load.** `OLLAMA_HTTP_TIMEOUT_SECONDS` is the per-request HTTP timeout (default 120). RQ still kills the worker function at `RQ_JOB_TIMEOUT_SECONDS`. Raise the RQ value for long chapters on CPU; raising only the HTTP timeout does not keep the job alive past RQ.

**Do not co-load NLLB.** When `TRANSLATION_PROVIDER=ollama`, the factory does not construct the NLLB engine. Do not also set the worker to load NLLB weights in the same process.
