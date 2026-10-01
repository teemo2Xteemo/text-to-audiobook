<!--
Sync Impact Report
- Version change: unversioned scaffold → 1.0.0
- Modified principles: placeholder principles → five project-specific principles
- Added sections: Product and Technical Constraints; Delivery Workflow and Quality Gates
- Removed sections: none
- Follow-up TODOs: none
-->
# Text-to-Audiobook Constitution

## Core Principles

### I. Language-Agnostic, Capability-Driven Product
Domain and application code MUST use `source_language`, `target_language`, BCP-47 language
tags, and provider capabilities. Concrete languages, language pairs, or voice identifiers MAY
appear only in provider adapters, configuration, test fixtures, and documented acceptance demos.
This preserves support for every provider-supported pair instead of making a demo pair a system
constraint.

### II. Ports Preserve Architectural Boundaries
The domain MUST own its types and ports; application code MUST orchestrate those ports; vendor,
storage, queue, and FFmpeg details MUST remain in providers or infrastructure. Dependencies MUST
flow `api → application → domain ← providers/infrastructure`. Changes MUST extend existing domain
ports and composition roots rather than duplicating interfaces, adding vendor branches to
orchestration, or creating parallel layers.

### III. Processing Is Durable, Asynchronous, and Chunked
HTTP endpoints MUST create jobs and return a `job_id`; workers MUST execute the pipeline outside
the request lifecycle. Long input MUST be processed as stable chunks with typed failures, retry,
checkpoint resume, and cache behavior that never regenerates a valid completed chunk. Pipeline
stages MUST remain separately testable so translation, narration, synthesis, normalization, and
merge failures can be isolated and recovered safely.

### IV. Security and Privacy Are Non-Negotiable
Uploads, filenames, paths, provider responses, and all user-controlled values MUST be treated as
untrusted. Process execution MUST use argument lists, never shell interpolation. Logs and errors
MUST exclude secrets, API keys, stack traces, and unnecessary story content. Credentials and
provider URLs MUST be supplied by configuration, and generated model weights, artifacts, and
secrets MUST NOT be committed.

### V. Evidence-Based Quality Gates
Behavior changes MUST include focused automated tests at the lowest suitable layer. Unit tests
MUST use deterministic fakes and MUST NOT require live providers, model downloads, GPU hardware,
or billing services. Integration tests MAY exercise external tools behind explicit markers.
Before delivery, relevant formatting, linting, type checks, policy scans, and tests MUST pass, or
the known failure and its impact MUST be reported.

## Product and Technical Constraints

The MVP is CPU-first and MUST run locally with Docker Compose without requiring a GPU. Its
established stack is React and TypeScript, FastAPI, Redis/RQ, FFmpeg, and Docker Compose; NLLB and
Edge TTS are replaceable provider adapters rather than domain dependencies. The default Compose
configuration MUST remain usable offline with fake providers.

The pipeline is input → language detect/select → parse → chunk → translate → narrate → synthesize
→ normalize/merge → output. Supported languages and compatible voices MUST be supplied through
capabilities, not frontend or domain constants. Public behavior, job-state transitions, cache
identity, checkpoint format, and provider substitution rules MUST retain the decisions recorded in
the accepted ADRs and implementation plan unless an approved architectural decision changes them.

## Delivery Workflow and Quality Gates

Before changing code, contributors MUST read the scoped Cursor rules, relevant AI guidance, and
affected ADRs; then search for existing ports, types, helpers, and tests. Ambiguity that changes
product behavior, public API, state, provider configuration, or architecture MUST document an
Assumption, Impact, Alternatives, and Recommendation before implementation.

Changes MUST be the smallest correct extension of the existing design. Backend changes require
the applicable `pytest -m "not integration"`, Ruff, and format checks. Frontend changes require
the applicable type check and unit tests. Repository-level changes require the applicable hook
self-test, policy scan, and Compose validation. A review MUST inspect the diff for secrets,
language-pair hard-coding, vendor leakage into domain/application code, and unrelated changes.

## Governance

This constitution governs development practice for this repository. Security takes precedence over
correctness, product requirements, accepted architecture, maintainability, scalability,
performance, and convenience. The product requirements, accepted ADRs, and scoped Cursor rules
provide binding detail; no lower-level guide may relax a principle in this constitution.

Amendments require a written rationale, an impact assessment on existing code and specifications,
and a version update. Versioning follows semantic intent: MAJOR for incompatible principle
removal or redefinition, MINOR for a new or materially expanded principle, and PATCH for
clarifications without governance change. Reviews MUST verify constitution compliance, and
implementation plans or ADRs MUST be updated when an amendment changes their decisions.

**Version**: 1.0.0 | **Ratified**: 2026-09-27 | **Last Amended**: 2026-09-27
