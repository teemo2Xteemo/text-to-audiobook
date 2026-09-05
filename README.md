# Multilingual story → audiobook

Web app that takes story text in a **user-selected source language** (or auto-detect) and produces narration audio in a **user-selected target language**.

Chinese → Vietnamese is a common example, not the only supported pair.

## Current status

- Product requirements: [`text-story-to-audiobook-requirements.md`](text-story-to-audiobook-requirements.md)
- AI coding rules: [`.cursor/rules/`](.cursor/rules/)
- Agent docs: [`docs/ai/`](docs/ai/)
- Implementation plan: [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md)
- Architecture decisions: [`docs/adr/`](docs/adr/)

**M1** (API + Redis Compose, `GET /health`), **M2** (domain contracts in `backend/app/domain`), **M3** (job HTTP, storage, enqueue), and **M4** (pipeline orchestrator with fakes) are in place. Next is **M5** (worker, DI, capabilities). Follow [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md). Stack: React + TypeScript, FastAPI, Redis/RQ, NLLB (CPU), Edge TTS, FFmpeg, Docker Compose ([ADR 0010](docs/adr/0010-stack-and-project-layout.md)).

## For coding agents

Start at [`AGENTS.md`](AGENTS.md) and [`docs/ai/README.md`](docs/ai/README.md). Do not hard-code languages or vendor SDKs into domain logic.

## Run

```bash
cp .env.example .env
docker compose up --build
```

API is bound to localhost: `http://127.0.0.1:8000/health` should return `{"status":"ok","service":"api"}`. Redis is on `127.0.0.1:6379`. FFmpeg: use the host `ffmpeg` on PATH if present, otherwise `backend/bin/ffmpeg`. The API image installs the binary via apt so Compose has PATH. Argv lists only; no Python ffmpeg binding.

Backend unit tests (Redis not required):

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```
