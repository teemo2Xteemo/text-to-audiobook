# Coding workflow

Future agents must follow this loop. Skipping “inspect existing code” is the usual cause of duplicate ports and hard-coded languages.

```text
Understand task
 → Read relevant .cursor/rules + docs/ai + matching ADRs
 → Search the repo for ports, enums, helpers, tests
 → Identify layer (api / application / domain / provider / worker / UI)
 → Plan the smallest correct change
 → Implement
 → Run unit tests (no live providers)
 → Formatter / linter / type checker when those tools exist
 → Review git diff for secrets, language hard-coding, vendor leaks
 → Report files, behavior, and open assumptions
```

## Planning bar

If the change adds a provider, a new job state, an env var, or a public API field, write the plan before coding. Include:

- Assumption
- Impact
- Alternatives
- Recommendation

## Smallest correct change

Prefer: extend an existing port, add one adapter, add tests next to the behavior.

Avoid: new frameworks, renaming the pipeline, “while I’m here” refactors, implementing Phase 3 voice cloning during an MVP bugfix.

## After tooling exists

From repo root (commands will land with the app; do not invent extra task runners):

- Backend: `ruff check` / `ruff format --check`, `pytest -m "not integration"` for `backend/tests`
- Frontend: `tsc --noEmit`, unit tests if present
- Policy: `python3 .cursor/hooks/selftest.py` and `python3 .github/scripts/scan_repo.py`
- Compose: `docker compose up` for integration only when asked; CI only validates `docker compose config`
- GitHub Actions: `.github/workflows/ci.yml` runs the same unit/lint/policy checks on `main` and pull requests

Hooks may auto-format after edits. They do **not** replace running tests.
