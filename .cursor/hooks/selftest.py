#!/usr/bin/env python3
"""Offline checks for project hooks. Run: python3 .cursor/hooks/selftest.py"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".cursor" / "hooks"
sys.path.insert(0, str(HOOKS))
import lib  # noqa: E402


def run(script: str, payload: dict) -> dict:
    proc = subprocess.run(
        ["python3", str(HOOKS / script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError((script, proc.returncode, proc.stderr, proc.stdout))
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def main() -> int:
    out = run(
        "session_start.py",
        {"session_id": "x", "is_background_agent": False, "composer_mode": "agent"},
    )
    assert "additional_context" in out
    assert "job_id" in out["additional_context"]

    out = run(
        "before_shell.py",
        {"command": "git push --force origin main", "cwd": str(ROOT), "sandbox": False},
    )
    assert out["permission"] == "deny", out

    out = run(
        "before_shell.py",
        {"command": "pytest -m \"not integration\"", "cwd": str(ROOT), "sandbox": False},
    )
    assert out["permission"] == "allow", out

    out = run(
        "before_shell.py",
        {
            "command": "sh -c 'ffmpeg -i /tmp/a.mp3 out.mp3'",
            "cwd": str(ROOT),
            "sandbox": False,
        },
    )
    assert out["permission"] == "ask", out

    out = run("after_file_edit.py", {"file_path": str(ROOT / "README.md"), "edits": []})
    assert out == {}

    bad_import = "from edge_tts import Communicate\n"
    findings = lib.scan_text("backend/app/domain/pipeline.py", bad_import)
    assert any(f.rule == "vendor_import_in_domain" for f in findings), findings

    findings = lib.scan_text("backend/app/providers/tts/edge.py", bad_import)
    assert not any(f.rule == "vendor_import_in_domain" for f in findings), findings

    findings = lib.scan_text(
        "frontend/src/App.tsx",
        'const VOICE = "vi-VN-NamMinhNeural";\n',
    )
    assert any(f.rule == "hardcoded_voice_id" for f in findings), findings

    findings = lib.scan_text(
        "docs/adr/0008-edge-tts-mvp-provider.md",
        'voice = "vi-VN-NamMinhNeural"\nfrom edge_tts import Communicate\n',
    )
    assert findings == [], findings

    findings = lib.scan_secrets('api_key = "sk-live-abcdefghijklmnopqrstuvwxyz0123"\n')
    assert findings, findings

    out = run(
        "post_tool_use.py",
        {
            "tool_name": "Write",
            "tool_input": {
                "path": str(ROOT / "backend/app/domain/jobs.py"),
                "contents": bad_import,
            },
            "workspace_roots": [str(ROOT)],
        },
    )
    assert "additional_context" in out
    assert "vendor_import_in_domain" in out["additional_context"], out

    out = run(
        "post_tool_use.py",
        {
            "tool_name": "Write",
            "tool_input": {
                "path": str(ROOT / "backend/app/domain/jobs.py"),
                "contents": "class JobStatus:\n    QUEUED = 'queued'\n",
            },
            "workspace_roots": [str(ROOT)],
        },
    )
    assert out == {}, out

    unsafe = 'subprocess.run("ffmpeg -i " + path, shell=True)\n'
    findings = lib.scan_unsafe_code(unsafe)
    assert any(f.rule == "shell_true" for f in findings), findings

    print("all hook checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
