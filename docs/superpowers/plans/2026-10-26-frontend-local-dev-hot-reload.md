# Frontend Local Dev Hot Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a fast, streamlined local frontend development workflow with instant Hot Module Replacement (HMR) by running backend services in Docker Compose and the Vite dev server on the host machine.

**Architecture:** Docker Compose boots only the backend services (`redis`, `api`, `worker`) exposing port 8000 to the host. The frontend runs directly on the host using Vite (`npm run dev`) on port 5173, with proxy routing `/api` and `/health` requests to `http://127.0.0.1:8000`.

**Tech Stack:** React 18, Vite 6, TypeScript, Docker Compose, Vitest.

**Spec:** [docs/superpowers/specs/2026-10-26-frontend-local-dev-hot-reload-design.md](file:///home/teemo/documents/projects/text-to-audiobook/docs/superpowers/specs/2026-10-26-frontend-local-dev-hot-reload-design.md)

## Global Constraints

- **Compose compatibility (ADR 0010):** `docker-compose.yml` must remain valid and pass `docker compose config --quiet`. The production `frontend` service remains intact.
- **Node 22 runtime:** Frontend development uses Node 22 (`npm ci` & `npm run dev`).
- **Path neutrality:** Frontend communicates via relative `/api` paths with Vite proxy to `http://127.0.0.1:8000`.

## Review Focus

1. **Proxy streaming & headers:** Vite proxy configuration forwards headers and streaming responses (such as `/api/jobs/{id}/audio`) without buffering or truncation.
2. **Backend-only compose startup:** Running `docker compose up -d redis api worker` starts only backend dependencies without attempting to build or start the `frontend` container.
3. **Hot reload responsiveness:** Editing a React component in `frontend/src/` triggers HMR in Vite without requiring browser refresh or container restart.
4. **Health & capabilities endpoint access:** `http://127.0.0.1:5173/health` and `http://127.0.0.1:5173/api/capabilities` correctly proxy to the backend and return valid JSON.
5. **No 502 stale DNS cache:** Unlike Nginx in Docker, Vite on host connecting to `127.0.0.1:8000` does not suffer from Docker DNS caching when the backend container restarts.

---

### Task 1: Vite Proxy Configuration Hardening & Audio Streaming Verification

**Files:**
- Modify: `frontend/vite.config.ts:6-12`
- Test: `frontend/src/api/client.test.ts` or `frontend/src/lib/jobUi.test.ts`

**Interfaces:**
- Consumes: `defineConfig` from `vitest/config`, `@vitejs/plugin-react`
- Produces: Hardened Vite dev server proxy configuration supporting streaming and header preservation

- [x] **Step 1: Inspect `frontend/vite.config.ts` and ensure proxy configuration handles `changeOrigin: true` for clean header handling**

```typescript
server: {
  port: 5173,
  proxy: {
    "/api": {
      target: "http://127.0.0.1:8000",
      changeOrigin: true,
    },
    "/health": {
      target: "http://127.0.0.1:8000",
      changeOrigin: true,
    },
  },
},
```

- [x] **Step 2: Run frontend test suite to ensure all unit tests and type checks pass**

Run: `cd frontend && npm test && npm run typecheck`  
Expected: All tests PASS with zero type errors.

- [x] **Step 3: Commit**

```bash
git add frontend/vite.config.ts
git commit -m "chore(frontend): configure vite dev proxy with changeOrigin for api and health"
```

---

### Task 2: Documentation and DX Guide in README.md

**Files:**
- Modify: `README.md:85-115`

**Interfaces:**
- Consumes: Hybrid workflow instructions from spec
- Produces: Updated `README.md` with a dedicated "Local UI Development with Hot Reload" section

- [x] **Step 1: Add a dedicated subsection in `README.md` under development instructions explaining the hybrid workflow**

Include:
1. Starting backend services only: `docker compose up -d redis api worker`
2. Starting Vite frontend on host: `cd frontend && npm install && npm run dev`
3. Opening `http://127.0.0.1:5173` with instant Hot Module Replacement (HMR) on UI edits.
4. Note contrasting this with full production Compose (`docker compose up --build` on port 8080) and explaining that Vite connects directly to `127.0.0.1:8000`, preventing Nginx 502 stale DNS cache issues.
5. Teardown command: `docker compose down`.

- [x] **Step 2: Verify markdown formatting and links in `README.md`**

Ensure all commands, ports, and file links are accurate and properly formatted.

- [x] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add hybrid local frontend development workflow with hot reload"
```

---

### Task 3: Full Verification of Configuration and CI Gates

**Files:**
- Verify: `docker-compose.yml`
- Verify: `frontend/`

- [x] **Step 1: Verify Docker Compose configuration syntax**

Run: `docker compose config --quiet`  
Expected: Exit code 0 (valid configuration).

- [x] **Step 2: Verify repository policy scans and hooks**

Run: `python3 .github/scripts/scan_repo.py && python3 .cursor/hooks/selftest.py`  
Expected: PASS with 0 violations.

- [x] **Step 3: Verify frontend tests and type checks**

Run: `cd frontend && npm test && npm run typecheck`  
Expected: PASS.
