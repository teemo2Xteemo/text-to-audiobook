# Design Document: Frontend Local Dev Hot Reload (Hybrid Mode)

**Date:** 2026-10-26  
**Status:** Approved by User  
**Scope:** Small Change  
**Author:** Pair Programming Session with User  

---

## 1. Intent & The Why

When developing UI features locally, developers currently run `docker compose up`, which builds and runs the frontend as a production Nginx container (`frontend/Dockerfile`). Because this container serves static pre-built assets and does not mount host source files, every UI code change requires stopping and rebuilding the Docker image (`docker compose up --build`).

This creates a slow feedback loop and disrupts frontend development flow. The developer wants an instantaneous Hot Reload (HMR) experience when editing frontend code.

The operator selected the **Hybrid Approach**: running backend services (`redis`, `api`, `worker`) in Docker containers while running the frontend development server (`npm run dev`) directly on the host machine.

---

## 2. Goals, Non-Goals, & Anti-Goals

### Goals
- Enable instantaneous UI Hot Module Replacement (HMR) for frontend development.
- Provide a clean, streamlined command workflow to boot only backend services (`redis`, `api`, `worker`) via Docker Compose.
- Verify and ensure Vite dev server proxy (`frontend/vite.config.ts`) routes all required paths (`/api`, `/health`, audio endpoints) smoothly to `http://127.0.0.1:8000`.
- Update `README.md` with explicit, copy-pasteable instructions for the hybrid hot reload development workflow.

### Non-Goals
- Modifying backend API business logic, domain contracts, or audio generation pipeline.
- Removing or altering the production `frontend` service in `docker-compose.yml` (production/eval deployment remains functional).
- Forcing host dependencies on production deployment environments.

### Anti-Goals
- Do not break existing CI checks (`docker compose config --quiet`, `ruff`, `pytest`, `npm test`, `npm run typecheck`).
- Do not introduce CORS issues or broken audio stream playback during local Vite dev.

---

## 3. Constraints & Repository Rules

- **Zero regression to Docker Compose (ADR 0010):** `docker-compose.yml` must remain valid and passing `docker compose config --quiet`.
- **Node 22 runtime:** Frontend development uses Node 22 (`npm ci` & `npm run dev`).
- **No hardcoded API endpoints:** Frontend continues using relative `/api` paths with Vite proxy to `http://127.0.0.1:8000`.

---

## 4. Approaches Considered & Tradeoffs

### Approach 1: Full-Docker Dev Container with Volume Mounts
- **Mechanism:** Add a dev stage or compose override to run Vite dev server inside Docker, mounting `./frontend` into `/app` and using anonymous volumes for `node_modules`.
- **Tradeoffs:**
  - *Pros:* Single command `docker compose up`, no Node requirement on host.
  - *Cons:* Slower HMR due to Docker file watcher overhead, heavier RAM usage, potential permission issues with `node_modules` between host and container.

### Approach 2 (Selected): Hybrid Model (Backend in Docker + Frontend on Host)
- **Mechanism:** Run `redis`, `api`, and `worker` in Docker Compose with exposed host ports (`127.0.0.1:8000` and `127.0.0.1:6379`). Run `npm run dev` directly on the host inside `frontend/`.
- **Tradeoffs:**
  - *Pros:* Maximum performance, instant sub-second Vite HMR, low RAM overhead, native IDE devtools & debugging.
  - *Cons:* Requires Node.js 22 installed on the host and running two commands/terminals.
- **Why chosen:** User explicitly chose Approach 2 for optimal speed and development ergonomics.

---

## 5. Technical Implementation Details

1. **Backend Service Launch:**
   - Command to launch backend: `docker compose up -d redis api worker` (or in foreground `docker compose up redis api worker`).
   - Port 8000 is already exposed to `127.0.0.1:8000` in `docker-compose.yml`.
   - Healthcheck on `api` and `redis` confirms backend readiness.

2. **Frontend Vite Configuration Verification:**
   - [`frontend/vite.config.ts`](../../frontend/vite.config.ts) is configured with `envDir: ".."` (to load root `.env`), explicit host binding, and proxy settings:
     ```ts
     server: {
       host: "127.0.0.1",
       port: 5173,
       strictPort: true,
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
     }
     ```
   - Verify that streaming audio endpoints (`/api/jobs/{id}/audio`) stream properly without proxy buffering issues in Vite.

3. **Convenience & Documentation:**
   - Add a dedicated section to `README.md` under development instructions explaining the hybrid workflow:
     1. Start backend: `docker compose up -d --build redis api worker`
     2. Start UI with HMR: `cd frontend && npm ci && npm run dev`
     3. Access UI at `http://127.0.0.1:5173`
   - (Optional helper) Add a convenience script in `package.json` or root if needed, or keep standard commands.

---

## 6. What's Left to the Builder

- Verify Vite dev server boots and proxies requests cleanly against live or test API endpoints.
- Update `README.md` with clear workflow guidance.
- Ensure all CI linters and configuration checks pass.
