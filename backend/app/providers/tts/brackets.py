from __future__ import annotations

import re

_EMPTY_RE = re.compile(r"\[\s*\]|【\s*】|〔\s*〕")
_OPEN_RE = re.compile(r"[\[【〔]")
_CLOSE_RE = re.compile(r"[\]】〕]")


def sanitize_brackets(text: str) -> str:
    """Engines read ``[x]`` as control tags; turn brackets into parentheses."""
    text = _EMPTY_RE.sub("", text)
    text = _CLOSE_RE.sub(")", _OPEN_RE.sub("(", text))
    return re.sub(r"\s+", " ", text).strip()
