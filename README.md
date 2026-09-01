# Multilingual story → audiobook

Web app that takes story text in a **user-selected source language** (or auto-detect) and produces narration audio in a **user-selected target language**.

Chinese → Vietnamese is a common example, not the only supported pair.

## Current status

This repository currently contains:

- Product requirements: [`text-story-to-audiobook-requirements.md`](text-story-to-audiobook-requirements.md)
- AI coding rules: [`.cursor/rules/`](.cursor/rules/)
- Hooks: [`.cursor/hooks.json`](.cursor/hooks.json)
- Agent docs: [`docs/ai/`](docs/ai/)
- Architecture decisions: [`docs/adr/`](docs/adr/)

**Application code is not implemented yet.** The intended stack is React + TypeScript, FastAPI, Redis/RQ, NLLB (CPU), Edge TTS, FFmpeg, Docker Compose. See [`docs/adr/0010-stack-and-project-layout.md`](docs/adr/0010-stack-and-project-layout.md).

## For coding agents

Start at [`AGENTS.md`](AGENTS.md) and [`docs/ai/README.md`](docs/ai/README.md). Do not hard-code languages or vendor SDKs into domain logic.

## Run

Once Compose exists:

```bash
docker compose up
```
