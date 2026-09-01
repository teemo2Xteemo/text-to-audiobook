#!/usr/bin/env python3
"""afterFileEdit: run formatters when they exist; no-op otherwise."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import emit, load_stdin  # noqa: E402

PY_EXTS = {".py"}
FRONT_EXTS = {".ts", ".tsx", ".js", ".jsx", ".json", ".css"}


def _run(argv: list[str]) -> None:
    try:
        subprocess.run(argv, check=False, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return


def format_file(path: Path) -> None:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        return
    ext = path.suffix.lower()
    if ext in PY_EXTS:
        ruff = shutil.which("ruff")
        if ruff:
            _run([ruff, "format", str(path)])
            return
        return
    if ext in FRONT_EXTS:
        prettier = shutil.which("prettier")
        if prettier:
            _run([prettier, "--write", "--log-level", "silent", str(path)])


def main() -> int:
    payload = load_stdin()
    file_path = payload.get("file_path")
    if isinstance(file_path, str) and file_path:
        format_file(Path(file_path))
    emit({})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
