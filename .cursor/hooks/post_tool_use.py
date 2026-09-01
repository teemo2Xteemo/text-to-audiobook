#!/usr/bin/env python3
"""postToolUse: inject findings after Write/StrReplace (secrets, unsafe shell, architecture)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import emit, format_context, load_stdin, repo_relative, scan_text  # noqa: E402


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("tool_input")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _file_path(tool_input: dict[str, Any]) -> str | None:
    for key in ("path", "file_path", "target_notebook"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _contents(path: str, tool_input: dict[str, Any]) -> str:
    contents = tool_input.get("contents")
    if isinstance(contents, str):
        return contents
    new_string = tool_input.get("new_string")
    disk = Path(path)
    if disk.is_file():
        try:
            return disk.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if isinstance(new_string, str):
        return new_string
    return ""


def main() -> int:
    payload = load_stdin()
    tool_input = _tool_input(payload)
    path = _file_path(tool_input)
    if not path:
        emit({})
        return 0

    text = _contents(path, tool_input)
    roots = payload.get("workspace_roots")
    roots_list = roots if isinstance(roots, list) else []
    rel = repo_relative(path, [str(r) for r in roots_list])
    findings = scan_text(rel, text)
    if not findings:
        emit({})
        return 0
    emit({"additional_context": format_context(rel, findings)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
