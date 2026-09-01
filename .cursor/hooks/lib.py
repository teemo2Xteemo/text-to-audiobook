"""Shared scanners for Cursor hooks. Deterministic, no network, stdlib only."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKIP_PATH_PARTS = (
    "/docs/",
    "/.cursor/",
    "/text-story-to-audiobook-requirements.md",
)

SKIP_SUFFIXES = (
    "text-story-to-audiobook-requirements.md",
    "AGENTS.md",
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem_private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("generic_bearer", re.compile(r"\b(?:sk-live|sk-proj)-[A-Za-z0-9_-]{16,}\b")),
    ("assigned_api_key", re.compile(
        r"""(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token)\b["']?\s*[:=]\s*["'][A-Za-z0-9_\-./+=]{20,}["']"""
    )),
)

UNSAFE_CODE_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "os_system",
        re.compile(r"\bos\.system\s*\("),
        "Use subprocess with an argument list; os.system is blocked.",
    ),
    (
        "shell_true",
        re.compile(r"shell\s*=\s*True"),
        "Do not use shell=True. Pass argv to subprocess.",
    ),
    (
        "ffmpeg_fstring",
        re.compile(r"""(?:f["'].*ffmpeg|ffmpeg.*\{)"""),
        "Do not interpolate values into an ffmpeg shell string. Use argv.",
    ),
    (
        "eval_exec",
        re.compile(r"\b(eval|exec)\s*\("),
        "eval/exec is not allowed in application code.",
    ),
)

VOICE_ID_RE = re.compile(
    r"""(?i)["'](?:[a-z]{2}-[A-Z]{2}-[\w]+Neural|p\d{3,}|eleven_[A-Za-z0-9_-]+)["']"""
)
PROVIDER_IF_RE = re.compile(
    r"""if\s+(?:\w+\.)?provider\s*==\s*["'][^"']+["']"""
)
VENDOR_IMPORT_RE = re.compile(
    r"""^\s*(?:from|import)\s+(edge_tts|elevenlabs|azure\.cognitiveservices|google\.cloud\.texttospeech|transformers|piper)""",
    re.MULTILINE,
)

DOMAINISH = ("/domain/", "/application/", "/api/")
ADAPTERISH = ("/providers/", "/infrastructure/", "/config/")
FRONTENDISH = ("/frontend/src/",)


@dataclass(frozen=True)
class Finding:
    severity: str  # error | warning
    rule: str
    message: str

    def format(self, path: str) -> str:
        return f"[{self.severity}] {self.rule} ({path}): {self.message}"


def load_stdin() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")


def repo_relative(file_path: str, workspace_roots: list[str] | None = None) -> str:
    path = Path(file_path)
    roots = [Path(r) for r in (workspace_roots or [])]
    for root in roots:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def should_skip_architecture(rel_path: str) -> bool:
    normalized = "/" + rel_path.replace("\\", "/").lstrip("/")
    if any(normalized.endswith(s) for s in SKIP_SUFFIXES):
        return True
    if any(part in normalized for part in SKIP_PATH_PARTS):
        return True
    if "/tests/" in normalized or "/test/" in normalized:
        return True
    return False


def is_adapter_path(rel_path: str) -> bool:
    n = "/" + rel_path.replace("\\", "/").lstrip("/")
    return any(p in n for p in ADAPTERISH)


def is_domainish_path(rel_path: str) -> bool:
    n = "/" + rel_path.replace("\\", "/").lstrip("/")
    if not n.startswith("/backend/"):
        return False
    return any(p in n for p in DOMAINISH) and not is_adapter_path(rel_path)


def looks_placeholder_secret(value: str) -> bool:
    lower = value.lower()
    return any(tok in lower for tok in ("changeme", "your_", "example", "placeholder", "xxx", "todo"))


def scan_secrets(text: str) -> list[Finding]:
    findings: list[Finding] = []
    if looks_placeholder_secret(text) and "BEGIN" not in text:
        # Still scan high-entropy tokens; skip only obvious placeholders per line below.
        pass
    for rule, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            snippet = match.group(0)
            if looks_placeholder_secret(snippet):
                continue
            findings.append(
                Finding(
                    "error",
                    rule,
                    "Possible secret in source. Use environment/config; never commit credentials.",
                )
            )
            break
    return findings


def scan_unsafe_code(text: str) -> list[Finding]:
    findings: list[Finding] = []
    for rule, pattern, message in UNSAFE_CODE_PATTERNS:
        if pattern.search(text):
            findings.append(Finding("error", rule, message))
    return findings


def scan_architecture(rel_path: str, text: str) -> list[Finding]:
    if should_skip_architecture(rel_path):
        return []
    findings: list[Finding] = []
    domainish = is_domainish_path(rel_path)
    adapter = is_adapter_path(rel_path)

    if domainish and VENDOR_IMPORT_RE.search(text):
        findings.append(
            Finding(
                "error",
                "vendor_import_in_domain",
                "Domain/application/API must not import vendor TTS/translation SDKs. Use ports + adapters.",
            )
        )
    if domainish and PROVIDER_IF_RE.search(text):
        findings.append(
            Finding(
                "error",
                "provider_switch_in_domain",
                "Do not branch on provider names in domain/application/API. Put that in adapters.",
            )
        )
    frontendish = any(p in ("/" + rel_path.replace("\\", "/").lstrip("/")) for p in FRONTENDISH)
    if (not adapter) and VOICE_ID_RE.search(text) and (domainish or frontendish):
        findings.append(
            Finding(
                "error",
                "hardcoded_voice_id",
                "Provider voice IDs belong in provider/config boundaries or capability APIs, not domain/application/API/UI constants.",
            )
        )
    return findings


def scan_text(rel_path: str, text: str) -> list[Finding]:
    if len(text) > 1_000_000:
        return []
    findings: list[Finding] = []
    normalized = "/" + rel_path.replace("\\", "/").lstrip("/")
    if not normalized.endswith("/selftest.py"):
        findings.extend(scan_secrets(text))
    if rel_path.endswith((".py", ".ts", ".tsx", ".js")):
        if not should_skip_architecture(rel_path):
            findings.extend(scan_unsafe_code(text))
            findings.extend(scan_architecture(rel_path, text))
    return findings


def format_context(path: str, findings: list[Finding]) -> str:
    lines = [
        "Governance hook found issues in the last edit. Fix before continuing:",
        *[f.format(path) for f in findings],
        "See .cursor/rules and docs/ai/architecture-rules.md.",
    ]
    return "\n".join(lines)
