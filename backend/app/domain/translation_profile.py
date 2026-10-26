from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TranslationProfile:
    id: str
    provider: str
    model: str
    label: str
    badge: str
    description: str
    is_default: bool = False
    supported_languages: tuple[str, ...] = field(default_factory=tuple)
