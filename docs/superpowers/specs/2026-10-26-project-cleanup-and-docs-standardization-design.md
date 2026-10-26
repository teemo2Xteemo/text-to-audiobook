# Design Document: Project Cleanup & Documentation Standardization

**Date:** 2026-10-26  
**Status:** In Review (Pending Builder Check & User Approval)  
**Scope:** Project  
**Author:** Pair Programming Session with User  

---

## 1. Intent & The Why

Over the course of developing multiple milestones and features (M1–M13 MVP, Speckit local translation, Gemini adapter, parallel chunk processing, timed SRT subtitles, translation profiles, studio UI, and hybrid hot reload), the repository accumulated various transient artifacts, deprecated experiment tooling, and documentation drift:

1. **Environment & Cache Clutter:** A leftover [node_modules/](../../../node_modules) directory in the project root (containing only an old Vite cache from September, while actual frontend code lives in [frontend/](../../../frontend)), lingering Playwright test artifacts in [.playwright-mcp/](../../../.playwright-mcp), local job outputs and translation caches in [storage/](../../../storage), and a merged worktree in [.worktrees/frontend-hot-reload](../../../.worktrees).
2. **Permission Conflicts:** `backend/.pytest_cache` and `backend/.ruff_cache` were created inside Docker with `nobody:nogroup` ownership, causing `Permission denied` errors when running `ruff` locally on the host.
3. **Obsolete / Duplicate Tooling (Speckit):** The `specs/` directory, `.specify/` framework, and 10 `.cursor/skills/speckit-*` skills were introduced during an experimental feature implementation (Feature 001: Optional Local Translation). That capability has long been accepted, codified in [docs/adr/0011-ollama-translategemma-optional-translation.md](../../../docs/adr/0011-ollama-translategemma-optional-translation.md), and integrated into the core backend and tests. Maintaining Speckit introduces unnecessary maintenance overhead and violates the single canonical layout defined in [docs/ai/target-structure.md](../../../docs/ai/target-structure.md).
4. **Documentation Drift:** [README.md](../../../README.md) and [docs/README.md](../../../docs/README.md) stopped updating at milestone M13, omitting major capabilities like the Gemini provider, parallel chunking, subtitle sync, and hot reload workflow.

The user requested a thorough cleanup across code, project structure, unnecessary file deletion, and documentation standardization to return the repository to a pristine, production-ready state.

---

## 2. Goals, Non-Goals, & Anti-Goals

### Goals
- **Prune unnecessary files & artifacts:** Remove root `node_modules/`, `.playwright-mcp/` artifacts, old test jobs/cache in `storage/`, the merged `.worktrees/frontend-hot-reload` worktree, and all local git branches already merged into `main`.
- **Fix cache permissions:** Remove permission-locked `.pytest_cache/` and `.ruff_cache/` in `backend/` so host-level development tools (`ruff`, `pytest`) run cleanly without errors.
- **Retire Speckit tooling:** Delete `specs/`, `.specify/`, and `.cursor/skills/speckit-*` to align the codebase strictly with [docs/ai/target-structure.md](../../../docs/ai/target-structure.md).
- **Standardize documentation:** Update [README.md](../../../README.md) and [docs/README.md](../../../docs/README.md) to document all existing features, architectural boundaries, and dev workflows. Explicitly recognize `docs/superpowers/` in [docs/ai/target-structure.md](../../../docs/ai/target-structure.md).
- **Verify zero regressions:** Ensure all backend tests (431+ passing), frontend tests (47 passing), linter checks, and Docker Compose configurations pass without issue.

### Non-Goals
- Altering core domain interfaces (`backend/app/domain`) or business logic.
- Rewriting or deleting existing Architecture Decision Records ([docs/adr/](../../../docs/adr/)) or historical governance documents ([docs/ai/](../../../docs/ai/)).
- Changing API contracts or frontend component behaviors.

### Anti-Goals
- Do not break the offline-first default (`fake` / `fake` providers).
- Do not remove files needed by Docker builds or CI workflows (`.github/workflows/ci.yml`).
- Do not leave untracked orphan files or broken relative links in documentation.

---

## 3. Constraints & Repository Rules

- **Target Structure Compliance ([docs/ai/target-structure.md](../../../docs/ai/target-structure.md)):** Every folder in the repository must be justified. Unnecessary parallel spec directories outside `docs/` must be removed.
- **Language-Agnostic & Provider Isolation ([AGENTS.md](../../../AGENTS.md)):** Domain code and documentation must maintain strict isolation from vendor SDKs.
- **Test Suite Green:** All existing unit, integration, and UI tests must pass before and after the cleanup.

---

## 4. Approaches Considered & Tradeoffs

### Approach 1 (Selected): Full Cleanup & Unified Architecture
- **Description:** 
  - Purge root `node_modules/`, `.playwright-mcp/`, `storage/` test jobs, merged worktrees, and merged local branches.
  - Fix permission-locked caches in `backend/`.
  - Delete `specs/`, `.specify/`, and `.cursor/skills/speckit-*`.
  - Update `README.md`, `docs/README.md`, and `target-structure.md` to reflect all current features and standard design locations.
- **Tradeoffs:**
  - *Pros:* Maximally clean, eliminates confusion for AI agents and human contributors, adheres strictly to `target-structure.md`, eliminates all permission errors.
  - *Cons:* Deletes Speckit templates and clarification logs (historical record remains in git history).
- **Why Chosen:** The user explicitly selected Approach 1 to achieve a clean, cohesive repository.

### Approach 2: Conservative Cleanup with Archival
- **Description:** Same cleanup of caches and worktrees, but move `specs/` and `.specify/` into `docs/archive/speckit/`.
- **Tradeoffs:**
  - *Pros:* Keeps historical Speckit markdown files directly accessible in the workspace tree.
  - *Cons:* Leaves obsolete documentation around, creating confusion about canonical specs and violating `target-structure.md`.
- **Why Rejected:** Redundant with Git commit history and existing ADR 0011.

---

## 5. Technical Specification & Implementation Plan

### Phase 1: Workspace & Artifact Purge
1. Remove root `node_modules/`:
   ```bash
   rm -rf node_modules
   ```
2. Clean [.playwright-mcp/](../../../.playwright-mcp):
   Remove all generated `.png`, `.yml`, `.log`, and `.mp3` files, keeping `.playwright-mcp/.gitignore` intact.
3. Clean [storage/](../../../storage):
   Remove all temporary job directories in `storage/jobs/*` and cache blobs in `storage/cache/*` while keeping `.gitkeep` or base directory structure intact.
4. Clean `.superpowers`:
   Remove unused `.superpowers/sdd/` if empty or preserve `.superpowers/sdd/.gitignore`.
5. Remove stale Git Worktrees:
   ```bash
   git worktree remove .worktrees/frontend-hot-reload
   ```
6. Prune merged local Git branches:
   Delete branches that have been merged into `main`:
   - `cursor/m1-api-redis-scaffold`
   - `cursor/m4-pipeline-orchestrator-fakes`
   - `cursor/playwright-mcp`
   - `cursor/speckit`
   - `feat/frontend-hot-reload`
   - `feat/frontend-studio-ui`
   - `feat/timed-srt-audio-sync`
   - `feat/translation-profile-selection`
   - `fix/ponytail-audit-remediation`

### Phase 2: Cache Permissions Remediation
1. Remove docker-owned cache directories:
   ```bash
   rm -rf backend/.pytest_cache backend/.ruff_cache .pytest_cache .ruff_cache
   ```
2. Re-run `ruff check backend` and `pytest backend/tests` on the host to regenerate cache directories with the host user's UID/GID.

### Phase 3: Speckit Retirement & Target Alignment
1. Remove Speckit directories and files:
   - `specs/` (entire tree)
   - `.specify/` (entire tree)
   - `.cursor/skills/speckit-*` (all 10 skills)
2. Remove any untracked Speckit metadata files (e.g. `.specify/feature.json`).

### Phase 4: Documentation Standardization & Update
1. **Update [README.md](../../../README.md):**
   - Add overview of newly added capabilities:
     - **Gemini Translation Provider:** Hosted cloud translation alternative (`TRANSLATION_PROVIDER=gemini`).
     - **Parallel Chunk Processing:** Asynchronous concurrent chunk translation and TTS pipeline.
     - **Timed SRT Subtitle Generation:** Automatic subtitle alignment and synchronized audio timeline generation.
     - **Translation Profile Selection:** Preset translation configurations for different literary styles.
     - **Studio UI & Hot Reload:** Dedicated studio controls, SRT cleaning, and hybrid local development workflow.
   - Update architecture diagram / milestone summary beyond M13.
2. **Update [docs/README.md](../../../docs/README.md):**
   - Link `docs/superpowers/specs/` and `docs/superpowers/plans/` as the standard repository location for feature designs and execution plans.
3. **Update [docs/ai/target-structure.md](../../../docs/ai/target-structure.md):**
   - Document `docs/superpowers/` in the required folder layout so future agents and contributors know it is an authorized part of the tree.

### Phase 5: Verification & Quality Gate
1. **Backend Linter & Format:**
   ```bash
   backend/.venv/bin/ruff check backend
   backend/.venv/bin/ruff format --check backend
   ```
2. **Backend Unit & Integration Tests:**
   ```bash
   backend/.venv/bin/pytest backend/tests
   ```
3. **Frontend Tests & Build:**
   ```bash
   cd frontend && npm test -- --run && npm run build
   ```
4. **Docker Compose Validation:**
   ```bash
   docker compose config --quiet
   ```
5. **Git Status Cleanliness:**
   Verify `git status` reflects only the intended deletions and doc updates, with no untracked residual files.

---

## 6. What's Left to the Builder

- Exact list of shell commands to execute the file deletions safely without removing needed directories.
- Minor wording polishes when synchronizing feature lists between `README.md` and `docs/README.md`.
