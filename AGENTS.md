# Agent instructions

This repository is a **multilingual story → audiobook** system. Product requirements live in `text-story-to-audiobook-requirements.md`. Application code is not the current contents of this repo; implement against the target architecture in `docs/ai/` and `.cursor/rules/`.

## Before writing code

1. Read the relevant Cursor rules in `.cursor/rules/`.
2. Read `docs/ai/README.md` and any ADR that touches the change.
3. Search the repo for existing ports, types, and tests. Reuse them.
4. Make the smallest correct change. Do not rewrite unrelated files.

## Non-negotiable constraints

- **Language-agnostic:** never hard-code a source language, target language, or language pair in business logic. Use `source_language`, `target_language`, and provider capabilities.
- **Provider ports:** domain code calls `TranslationProvider` / `TTSProvider` interfaces, not Edge TTS, NLLB, or any other vendor API.
- **Async jobs:** HTTP creates a job and returns `job_id`. Workers run the pipeline.
- **Chunks:** long stories are processed per chunk with retry, cache, checkpoint, and resume. Do not regenerate successful chunks.
- **Security:** treat uploads and filenames as untrusted. Never interpolate user input into a shell command. Never commit secrets.

## Workflow

Understand → read rules → inspect code → plan → smallest change → tests → formatter/linter/typecheck → review diff → report.

Details: `docs/ai/coding-workflow.md`. Hook behavior: `docs/ai/hooks.md`.
