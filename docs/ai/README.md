# AI coding governance

This directory is the human-readable companion to Cursor rules (`.cursor/rules/`) and hooks (`.cursor/hooks.json`).

The product is a **multilingual story → audiobook** pipeline. Requirements: [`text-story-to-audiobook-requirements.md`](../../text-story-to-audiobook-requirements.md). Chinese → Vietnamese is an **example acceptance path**, not the system design.

## Read order for agents

1. [`AGENTS.md`](../../AGENTS.md) — short non-negotiables
2. `.cursor/rules/00-core.mdc` and `01-architecture.mdc`
3. This folder, then the ADR that matches the change
4. Scoped rules for the files you will edit

| Doc | Purpose |
| --- | --- |
| [architecture-rules.md](architecture-rules.md) | Why the architecture exists and requirements conflicts |
| [target-structure.md](target-structure.md) | Intended repo layout (not yet implemented) |
| [coding-workflow.md](coding-workflow.md) | Step-by-step agent workflow |
| [provider-development.md](provider-development.md) | How to add translation/TTS adapters |
| [testing-strategy.md](testing-strategy.md) | What to test and how to isolate providers |
| [hooks.md](hooks.md) | What hooks do, when they fire, future hooks |
| [../adr/README.md](../adr/README.md) | Architecture Decision Records |

## What this repo is today

Governance only: rules, hooks, docs, ADRs. **Do not treat missing `backend/` or `frontend/` as a license to invent an unrelated stack.** Follow `target-structure.md` and ADR 0010.
