# Multilingual story → audiobook

Web app that takes story text in a **user-selected source language** (or auto-detect) and produces narration audio in a **user-selected target language**.

Chinese → Vietnamese is a common example, not the only supported pair.

## Current status

- Product requirements: [`text-story-to-audiobook-requirements.md`](text-story-to-audiobook-requirements.md)
- AI coding rules: [`.cursor/rules/`](.cursor/rules/)
- Agent docs: [`docs/ai/`](docs/ai/)
- Implementation plan: [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md)
- Architecture decisions: [`docs/adr/`](docs/adr/)

**M1**–**M11** are in place (API + Redis Compose, domain contracts, job HTTP/enqueue, pipeline orchestrator, RQ worker + DI + capabilities, Vite job UI, conservative narration, NLLB CPU adapter, Edge TTS + FFmpeg normalize, chunk retry, checkpoint resume). Next is **M12** (translation/TTS cache). Follow [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md). Stack: React + TypeScript, FastAPI, Redis/RQ, NLLB (CPU), Edge TTS, FFmpeg, Docker Compose ([ADR 0010](docs/adr/0010-stack-and-project-layout.md)).

## For coding agents

Start at [`AGENTS.md`](AGENTS.md) and [`docs/ai/README.md`](docs/ai/README.md). Do not hard-code languages or vendor SDKs into domain logic.

## Run

```bash
cp .env.example .env
docker compose up --build
```

API is bound to localhost: `http://127.0.0.1:8000/health` should return `{"status":"ok","service":"api"}`. The job UI is at `http://127.0.0.1:8080`. Redis is on `127.0.0.1:6379`. Compose services: **frontend + api + redis + worker**. FFmpeg: host `ffmpeg` on PATH if present, otherwise `backend/bin/ffmpeg`; the **worker** image installs the binary via apt (slim API image does not). Argv lists only; no Python ffmpeg binding. Default providers are `fake` / `fake` for offline boot.

GitHub Actions (`.github/workflows/ci.yml`) runs unit tests, linters, and a secret/policy scan on `main` and pull requests. It skips `@pytest.mark.integration` (real FFmpeg).

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
