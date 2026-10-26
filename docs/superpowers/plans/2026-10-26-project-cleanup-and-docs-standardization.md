# Project Cleanup & Documentation Standardization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clean up the repository workspace (purge root `node_modules`, Playwright MCP artifacts, test jobs in `storage/`, merged worktrees, and merged branches), fix Docker-owned cache permissions in `backend/`, retire the obsolete Speckit tooling (`specs/`, `.specify/`, `speckit-*` skills) to conform to `target-structure.md`, and update project documentation to reflect all current features and architectures.

**Architecture:** Systematic 5-task cleanup following the accepted design in `docs/superpowers/specs/2026-10-26-project-cleanup-and-docs-standardization-design.md`. Each task executes an isolated layer of cleanup or documentation update, verifies the resulting state immediately with exact commands, and maintains full compatibility with Docker Compose, backend tests, and frontend builds.

**Tech Stack:** Bash, Git, Python 3.12+ (Ruff, Pytest), Node 22 (Vite, Vitest), Docker Compose.

**Spec:** [`docs/superpowers/specs/2026-10-26-project-cleanup-and-docs-standardization-design.md`](../specs/2026-10-26-project-cleanup-and-docs-standardization-design.md)

## Global Constraints

- Never break or modify domain contracts in `backend/app/domain` or pipeline logic in `backend/app/application`.
- Keep Docker Compose (`docker-compose.yml`) valid and runnable with offline fakes (`fake` / `fake`).
- Preserve `.playwright-mcp/.gitignore` and directory structure in `storage/` so future tests/runs do not fail on missing mount roots.
- All 431+ backend tests and 47 frontend tests must pass without skips or regressions.
- Every deleted file or directory must be either git-tracked (removable via git) or strictly an uncommitted transient artifact.

## Review Focus

- Deleting root `node_modules` must not touch `frontend/node_modules/`.
- Deleting `backend/.pytest_cache` and `backend/.ruff_cache` must resolve permission errors for host users without breaking Docker volume permissions.
- Removing Speckit must delete all 10 `speckit-*` skills in `.cursor/skills/` without touching other `.cursor/` configurations (agents, hooks, rules, mcp.json).
- Updating `README.md` must accurately present Gemini, parallel chunking, SRT subtitles, and hybrid dev mode without contradicting ADR 0001–0011.
- Removing `.worktrees/frontend-hot-reload` must be executed via `git worktree remove` before deleting the local branch.

---

### Task 1: Prune Root node_modules, Playwright MCP Artifacts, and storage/ Test Jobs

**Files:**
- Remove: `node_modules/`
- Clean: `.playwright-mcp/` (remove `*.png`, `*.yml`, `*.log`, `*.mp3`, keep `.gitignore`)
- Clean: `storage/jobs/*`, `storage/cache/*` (keep `storage/` and `.gitignore`)

**Interfaces:**
- Consumes: Existing workspace directories.
- Produces: Clean workspace free of untracked build artifacts and transient run outputs.

- [ ] **Step 1: Remove root `node_modules/`**

Run:
```bash
rm -rf node_modules
```

- [ ] **Step 2: Clean transient test files from `.playwright-mcp/`**

Run:
```bash
find .playwright-mcp -type f ! -name '.gitignore' -delete
```

- [ ] **Step 3: Clean old job directories and translation cache files from `storage/`**

Run:
```bash
rm -rf storage/jobs/* storage/cache/translation/* storage/cache/tts/*
```

- [ ] **Step 4: Verify workspace artifact cleanup**

Run:
```bash
test ! -d node_modules && ls -la .playwright-mcp && find storage/ -maxdepth 2
```
Expected:
- `node_modules` does not exist.
- `.playwright-mcp` contains only `.gitignore`.
- `storage/jobs` and `storage/cache` are empty directories.

---

### Task 2: Remove Stale Git Worktrees and Prune Merged Local Branches

**Files:**
- Remove Worktree: `.worktrees/frontend-hot-reload`
- Delete Local Branches:
  - `feat/frontend-hot-reload`
  - `cursor/m1-api-redis-scaffold`
  - `cursor/m4-pipeline-orchestrator-fakes`
  - `cursor/playwright-mcp`
  - `cursor/speckit`
  - `feat/frontend-studio-ui`
  - `feat/timed-srt-audio-sync`
  - `feat/translation-profile-selection`
  - `fix/ponytail-audit-remediation`

**Interfaces:**
- Consumes: Git repository state.
- Produces: Git repository with only active branch (`main`) and no orphan worktrees.

- [ ] **Step 1: Remove the merged git worktree**

Run:
```bash
git worktree remove .worktrees/frontend-hot-reload
```

- [ ] **Step 2: Delete local branches merged into `main`**

Run:
```bash
git branch -d feat/frontend-hot-reload cursor/m1-api-redis-scaffold cursor/m4-pipeline-orchestrator-fakes cursor/playwright-mcp cursor/speckit feat/frontend-studio-ui feat/timed-srt-audio-sync feat/translation-profile-selection fix/ponytail-audit-remediation
```

- [ ] **Step 3: Verify git worktree and branch cleanliness**

Run:
```bash
git worktree list && git branch --merged main
```
Expected:
- Worktree list displays only `/home/teemo/documents/projects/text-to-audiobook` on `[main]`.
- Merged branch list displays only `* main`.

---

### Task 3: Remediate Cache Permissions in backend/

**Files:**
- Remove: `backend/.pytest_cache`, `backend/.ruff_cache`, `.pytest_cache`, `.ruff_cache`

**Interfaces:**
- Consumes: File system permissions on cache dirs.
- Produces: Clean cache directories owned by the host developer.

- [ ] **Step 1: Delete docker-owned cache directories**

Run:
```bash
rm -rf backend/.pytest_cache backend/.ruff_cache .pytest_cache .ruff_cache
```

- [ ] **Step 2: Regenerate cache with host user permissions using Ruff and Pytest**

Run:
```bash
backend/.venv/bin/ruff check backend && backend/.venv/bin/ruff format --check backend
```
Expected:
- `All checks passed!`
- `112 files already formatted`
- No `Permission denied` errors.

---

### Task 4: Retire Speckit Framework, Specs, and Skills

**Files:**
- Delete: `specs/` (entire directory tree)
- Delete: `.specify/` (entire directory tree)
- Delete: `.cursor/skills/speckit-*` (all 10 Speckit skills)

**Interfaces:**
- Consumes: Repository root and `.cursor/skills/`.
- Produces: Repository structure matching `docs/ai/target-structure.md`.

- [ ] **Step 1: Remove `specs/` directory**

Run:
```bash
git rm -r specs/
```

- [ ] **Step 2: Remove `.specify/` directory**

Run:
```bash
git rm -r .specify/
rm -f .specify/feature.json
```

- [ ] **Step 3: Remove Speckit skills from `.cursor/skills/`**

Run:
```bash
git rm -r .cursor/skills/speckit-*
```

- [ ] **Step 4: Verify Speckit deletion**

Run:
```bash
test ! -d specs && test ! -d .specify && test ! -d .cursor/skills/speckit-plan
```
Expected: Command exits with status 0.

- [ ] **Step 5: Commit Speckit removal**

Run:
```bash
git commit -m "chore: retire speckit framework and align with target-structure"
```

---

### Task 5: Standardize and Update Documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/ai/target-structure.md`

**Interfaces:**
- Consumes: Current feature state and directory layout.
- Produces: Accurate, unified project documentation.

- [ ] **Step 1: Update `docs/ai/target-structure.md` to document `docs/superpowers/`**

Edit [`docs/ai/target-structure.md`](../../ai/target-structure.md):
Add `docs/superpowers/` (specifications and plans for ongoing features) to the target directory tree diagram.

- [ ] **Step 2: Update `docs/README.md`**

Edit [`docs/README.md`](../../README.md):
Add reference to `docs/superpowers/` for active feature specifications and implementation plans.

- [ ] **Step 3: Update `README.md`**

Edit [`README.md`](../../../README.md):
- Update the feature list to include:
  - Gemini Translation Adapter (`TRANSLATION_PROVIDER=gemini`)
  - Parallel Chunk Processing (concurrent chunk translation & speech synthesis)
  - Timed SRT Subtitle Synchronization and audio timeline merging
  - Translation Profile Selection (literary presets)
  - Studio UI and Hybrid Local Development Workflow (`npm run dev` with HMR)
- Verify all links to files exist.

- [ ] **Step 4: Commit documentation updates**

Run:
```bash
git add docs/ai/target-structure.md docs/README.md README.md
git commit -m "docs: update readme and target-structure with latest features and superpowers specs"
```

---

### Task 6: Full Quality Gate Verification

**Files:** None (verification task)

**Interfaces:**
- Consumes: Cleaned repository.
- Produces: Verified passing test suite, formatting, and Docker Compose configuration.

- [ ] **Step 1: Run backend Ruff lint and format check**

Run:
```bash
backend/.venv/bin/ruff check backend && backend/.venv/bin/ruff format --check backend
```
Expected: PASS with 0 errors.

- [ ] **Step 2: Run backend Pytest suite**

Run:
```bash
backend/.venv/bin/pytest backend/tests
```
Expected: 431+ passed, 6 skipped, 0 failed.

- [ ] **Step 3: Run frontend Vitest test suite**

Run:
```bash
cd frontend && npm test -- --run
```
Expected: 7 test files passed, 47 tests passed.

- [ ] **Step 4: Run frontend build check**

Run:
```bash
cd frontend && npm run build
```
Expected: TypeScript check passes, Vite builds `dist/` successfully.

- [ ] **Step 5: Verify Docker Compose configuration**

Run:
```bash
docker compose config --quiet
```
Expected: Exits with status 0.

- [ ] **Step 6: Verify Git status is clean**

Run:
```bash
git status
```
Expected: Working tree clean (nothing untracked or modified).
