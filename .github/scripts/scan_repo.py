#!/usr/bin/env python3
"""Scan the working tree the way CI will: tracked and unignored files only."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".cursor" / "hooks"
sys.path.insert(0, str(HOOKS))
from lib import Finding, scan_text  # noqa: E402

SKIP_SUFFIXES = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".pdf",
    ".zip",
    ".gz",
    ".whl",
    ".so",
    ".pyc",
)


def listed_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    names = [name for name in proc.stdout.decode("utf-8").split("\0") if name]
    return [ROOT / name for name in names]


def is_skippable(path: Path) -> bool:
    if not path.is_file():
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return False


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        print(f"skip unreadable {path.relative_to(ROOT).as_posix()}: {exc}", file=sys.stderr)
        return None
    if b"\0" in data[:1024]:
        return None
    if len(data) > 1_000_000:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def main() -> int:
    findings: list[tuple[str, Finding]] = []
    for path in listed_files():
        if is_skippable(path):
            continue
        text = read_text(path)
        if text is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for finding in scan_text(rel, text):
            findings.append((rel, finding))

    if not findings:
        print("policy scan passed")
        return 0

    print("policy scan failed:", file=sys.stderr)
    for rel, finding in findings:
        print(finding.format(rel), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
