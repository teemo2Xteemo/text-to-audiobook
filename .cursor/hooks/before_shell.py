#!/usr/bin/env python3
"""beforeShellExecution: block unsafe shell, FFmpeg interpolation, and force-push."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import emit, load_stdin  # noqa: E402

DENY: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bgit\s+push\b.*(?:--force\b|\s-f\b)"),
        "Force-push is blocked by project hooks. Ask the user explicitly if it is required.",
    ),
    (
        re.compile(r"\brm\s+(-rf|-fr)\s+/\s*$"),
        "Refusing recursive delete of /.",
    ),
)

ASK: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"""(sh\s+-c|bash\s+-c|zsh\s+-c).*\bffmpeg\b""", re.I),
        "ffmpeg via sh -c can interpolate untrusted paths. Prefer subprocess argv lists.",
    ),
    (
        re.compile(r"""ffmpeg[^;\n]*[`$]"""),
        "ffmpeg command appears to expand shell values. Confirm paths are job-scoped, not user-interpolated.",
    ),
    (
        re.compile(r"""\becho\s+\$\{?(OPENAI|ELEVEN|AZURE|API|SECRET|TOKEN)""", re.I),
        "This may print a secret. Confirm it is necessary.",
    ),
)


def main() -> int:
    payload = load_stdin()
    command = str(payload.get("command") or "")
    if not command.strip():
        emit({"permission": "allow"})
        return 0

    for pattern, message in DENY:
        if pattern.search(command):
            emit(
                {
                    "permission": "deny",
                    "user_message": message,
                    "agent_message": message,
                }
            )
            return 0

    for pattern, message in ASK:
        if pattern.search(command):
            emit(
                {
                    "permission": "ask",
                    "user_message": message,
                    "agent_message": message,
                }
            )
            return 0

    emit({"permission": "allow"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
