# Target structure

Required layout. Create **missing** folders from this tree; do not invent a parallel one. **M1**–**M6** already exist (`domain`, job HTTP, pipeline orchestrator, RQ worker, capabilities, Compose worker, Vite job UI). Fill later layers in milestone order (`implementation-plan.md`). Next is **M7**. Reuse existing ports — do not re-scaffold `domain/`.

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
│   ├── Dockerfile            # Slim API (no PyTorch / FFmpeg)
│   └── Dockerfile.worker     # Worker + FFmpeg (torch/Edge later)
├── frontend/
│   ├── src/
│   ├── package.json
│   ├── nginx.conf            # same-origin /api + /health → api
│   └── Dockerfile
├── storage/                  # gitignored job artifacts
├── docker-compose.yml
├── docs/ai/                  # this folder
├── docs/adr/
├── .github/                  # CI workflow + secret/policy scan script
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
