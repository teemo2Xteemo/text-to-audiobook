#!/usr/bin/env python3
"""sessionStart: inject non-negotiable architecture constraints."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import emit, load_stdin  # noqa: E402

CONTEXT = """Multilingual story→audiobook project. Non-negotiable: never hard-code source/target language or a language pair in business logic; call TranslationProvider/TTSProvider ports; HTTP returns job_id and workers run the pipeline; chunks support retry/cache/resume. Read .cursor/rules and docs/ai/README.md before implementing. Do not implement unrelated features."""


def main() -> int:
    load_stdin()
    emit({"additional_context": CONTEXT})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
