# Target structure

The application is **not implemented yet**. New code should create this tree rather than a novel layout.

```text
.
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI routers, request/response schemas
│   │   ├── application/      # Use-cases / pipeline orchestration
│   │   ├── domain/           # Ports, job state, chunking, errors
│   │   ├── infrastructure/   # Redis, filesystem storage, FFmpeg runner
│   │   ├── providers/        # Translation + TTS adapters
│   │   │   ├── translation/
│   │   │   └── tts/
│   │   ├── workers/          # Queue consumers
│   │   └── config/           # Settings from environment
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   ├── package.json
│   └── Dockerfile
├── storage/                  # gitignored job artifacts
├── docker-compose.yml
├── docs/ai/                  # this folder
├── docs/adr/
├── .cursor/rules/
└── text-story-to-audiobook-requirements.md
```

## Layer rules

| Layer | May depend on | Must not |
| --- | --- | --- |
| `api` | application, schemas | providers, FFmpeg, Redis clients directly |
| `application` | domain ports, injected adapters | vendor SDKs, FastAPI Request objects |
| `domain` | stdlib + domain types | FastAPI, Redis, Edge TTS, NLLB, FFmpeg |
| `providers` | domain ports, provider SDKs | HTTP routers, other providers’ internals |
| `infrastructure` | domain types, Redis/FS/FFmpeg | product policy (chunk meaning, language rules) |
| `workers` | application services | duplicating pipeline logic already in application |

If a proposed folder is not in this tree, justify it in an ADR or do not add it.
