# Architecture Decision Records

ADRs record *why* a durable choice was made. Add a new file when you change ports, job state, storage, or the MVP stack. Do not add ADRs for routine bugfixes.

| ID | Decision |
| --- | --- |
| [0001](0001-language-agnostic-architecture.md) | Language-agnostic domain; no hard-coded pairs |
| [0002](0002-provider-abstraction.md) | Translation/TTS ports and adapters |
| [0003](0003-async-job-architecture.md) | HTTP jobs + Redis worker |
| [0004](0004-chunk-based-processing.md) | Stable chunks with incremental I/O |
| [0005](0005-translation-narration-tts-separation.md) | Three pipeline stages |
| [0006](0006-cache-strategy.md) | Cache identity includes languages and voice |
| [0007](0007-retry-and-resume.md) | Per-chunk retry and checkpoint resume |
| [0008](0008-edge-tts-mvp-provider.md) | Edge TTS as first adapter, not the model |
| [0009](0009-cpu-first-mvp.md) | CPU / 16GB / no GPU constraint |
| [0010](0010-stack-and-project-layout.md) | FastAPI, React, Redis, Compose layout |

Status values: Proposed, Accepted, Superseded.
