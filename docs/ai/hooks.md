# Hooks

Project hooks: `.cursor/hooks.json` (schema version 1). Commands run from the **repository root** via `python3 .cursor/hooks/...` (stdlib only). They are deterministic, idempotent, and fail **open** except where a script explicitly returns `permission: deny`.

Do not add prompt-hooks for architecture taste. If a check needs judgment, put it in a Cursor rule or ADR.

## Active hooks

### sessionStart → `session_start.py`

- **Purpose:** Inject the five non-negotiables (languages, ports, jobs, chunks, security pointer).
- **Trigger:** New agent conversation.
- **Output:** `additional_context`.
- **Failure:** Ignored (sessionStart is fire-and-forget).

### beforeShellExecution → `before_shell.py`

- **Purpose:** Block force-push and `rm -rf /`; ask on `ffmpeg` via `sh -c` or shell expansion, and on echoing credential env vars.
- **Trigger:** Agent shell commands.
- **Output:** `{ permission, user_message, agent_message }`.
- **Failure:** Fail-open (command runs) unless Cursor is configured otherwise. This hook does **not** set `failClosed` so a parser bug cannot freeze the terminal.

### afterFileEdit → `after_file_edit.py`

- **Purpose:** Format Python with `ruff format` and frontend files with `prettier` **if those binaries exist**.
- **Trigger:** Agent file edits.
- **Command:** no-op when tools are missing (current repo has no formatter toolchain yet).
- **Failure:** Fail-open; never install packages from the hook.

### postToolUse (Write / StrReplace / EditNotebook) → `post_tool_use.py`

- **Purpose:** Scan the written file for secrets, `os.system` / `shell=True` / interpolated ffmpeg, vendor imports in domain/application/API, provider `if` switches, hard-coded neural voice IDs in domain or frontend src.
- **Trigger:** After those tools succeed.
- **Output:** `additional_context` listing findings (does not rewrite the file).
- **Failure:** Fail-open; empty JSON on parse errors.

Docs, `.cursor/`, requirements, and tests are skipped for architecture and unsafe-code checks so examples in rules/ADRs/hooks do not self-flag. Secret patterns still apply, except `.cursor/hooks/selftest.py`.

## Recommended hooks (not enabled — tooling missing)

Enable these when the corresponding commands exist and stay under ~30s:

| When | Hook | Command (planned) |
| --- | --- | --- |
| After backend Python is scaffolded | `stop` or `afterFileEdit` | `ruff check` on changed files |
| After `pyproject.toml` + tests | `stop` (loop_limit 1) | `pytest -m "not integration"` from the repo root (root `pytest.ini`) if the change is under `backend/` |
| After frontend scaffold | `stop` | `npx tsc --noEmit` in `frontend/` |
| CI (not a Cursor hook) | GitHub Actions (`.github/workflows/ci.yml`) | unit tests + linters + secret/policy scan |

A `stop` hook that auto-`followup_message`s on test failure is useful later but can loop; cap with `loop_limit: 1`.

## Local dry-run

```bash
python3 .cursor/hooks/selftest.py
echo '{"command":"git push --force origin main"}' | python3 .cursor/hooks/before_shell.py
```

See also `docs/ai/coding-workflow.md`.
