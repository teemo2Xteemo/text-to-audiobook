# Multilingual story → audiobook

Web app that takes story text in a **user-selected source language** (or auto-detect) and produces narration audio in a **user-selected target language**.

Chinese → Vietnamese is a common example, not the only supported pair.

## Current status

- Product requirements: [`text-story-to-audiobook-requirements.md`](text-story-to-audiobook-requirements.md)
- AI coding rules: [`.cursor/rules/`](.cursor/rules/)
- Agent docs: [`docs/ai/`](docs/ai/)
- Implementation plan: [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md)
- Architecture decisions: [`docs/adr/`](docs/adr/)

**M1**–**M5** are in place (API + Redis Compose, domain contracts, job HTTP/enqueue, pipeline orchestrator with fakes, RQ worker + DI + capabilities + Compose worker). Next is **M6** (frontend job UI). Follow [`docs/ai/implementation-plan.md`](docs/ai/implementation-plan.md). Stack: React + TypeScript, FastAPI, Redis/RQ, NLLB (CPU), Edge TTS, FFmpeg, Docker Compose ([ADR 0010](docs/adr/0010-stack-and-project-layout.md)).

## For coding agents

Start at [`AGENTS.md`](AGENTS.md) and [`docs/ai/README.md`](docs/ai/README.md). Do not hard-code languages or vendor SDKs into domain logic.

## Run

```bash
cp .env.example .env
docker compose up --build
```

API is bound to localhost: `http://127.0.0.1:8000/health` should return `{"status":"ok","service":"api"}`. Redis is on `127.0.0.1:6379`. Compose services: **api + redis + worker**. FFmpeg: host `ffmpeg` on PATH if present, otherwise `backend/bin/ffmpeg`; the **worker** image installs the binary via apt (slim API image does not). Argv lists only; no Python ffmpeg binding. Default providers are `fake` / `fake` for offline boot.

Backend unit tests (Redis not required):

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```
