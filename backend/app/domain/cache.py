from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from app.domain.audio import TTSSettings

CACHE_OPERATIONS = frozenset({"translation", "tts"})


def build_cache_key(
    *,
    operation: str,
    text: str,
    source_language: str,
    target_language: str,
    provider: str,
    model: str,
    voice: str,
    settings: TTSSettings | Mapping[str, Any],
) -> str:
    if operation not in CACHE_OPERATIONS:
        raise ValueError(f"unsupported cache operation: {operation}")
    payload = {
        "model": model,
        "operation": operation,
        "provider": provider,
        "settings": _canonical_settings(settings),
        "source_language": source_language,
        "target_language": target_language,
        "text": text,
        "voice": voice,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_settings(settings: TTSSettings | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(settings, TTSSettings):
        return asdict(settings)
    if is_dataclass(settings) and not isinstance(settings, type):
        return asdict(settings)
    return {str(key): settings[key] for key in settings}
