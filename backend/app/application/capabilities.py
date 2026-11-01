from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from app.domain.audio import Voice
from app.domain.ports import TranslationProvider, TTSProvider
from app.domain.translation_profile import TranslationProfile

_DEFAULT_ESTIMATE_DEFAULTS: dict[str, float] = {
    "chars_per_second": 15.0,
    "preview_wpm": 150.0,
}


# id -> (label, kind, cost_label)
logger = logging.getLogger(__name__)

_ENGINE_META: dict[str, tuple[str, Literal["online", "local"], str]] = {
    "edge": ("Edge", "online", "Free"),
    "fish": ("Fish Audio", "online", "Free · fair use"),
    "vieneu": ("VieNeu-TTS", "local", "Local · CPU"),
    "fake": ("Fake", "local", "Free"),
}


def engine_label(engine: str) -> str:
    return _ENGINE_META.get(engine, (engine, "online", ""))[0]


async def engine_available(provider: TTSProvider) -> bool:
    """True unless the provider has a ``check_available`` that raises."""
    check = getattr(provider, "check_available", None)
    if check is None:
        return True
    try:
        await check()
    except Exception as exc:  # any probe failure means unavailable, never a 500
        logger.warning(
            "tts_engine_probe_failed",
            extra={"engine": type(provider).__name__, "error": type(exc).__name__},
        )
        return False
    return True


@dataclass(frozen=True)
class TtsEngineInfo:
    id: str
    label: str
    kind: Literal["online", "local"]
    cost_label: str
    available: bool
    languages: list[str]
    max_concurrency: int


@dataclass(frozen=True)
class Capabilities:
    languages: list[str]
    voices: list[Voice]
    translation_profiles: list[TranslationProfile] = field(default_factory=list)
    narrate_languages: list[str] = field(default_factory=list)
    estimate_defaults: dict[str, float] = field(default_factory=dict)
    tts_engines: list[TtsEngineInfo] = field(default_factory=list)


class CapabilitiesService:
    """Languages = translation ∩ TTS (at least one voice).

    Unfiltered voices returns all TTS voices sorted by language code with default voice first
    per language (so narrate mode can access TTS-only locales); or voices are filtered by
    optional language parameter.
    """

    def __init__(
        self,
        *,
        translation: TranslationProvider,
        tts: TTSProvider,
        tts_engines: Mapping[str, TTSProvider | None] | None = None,
        default_tts_engine: str = "fake",
        translation_profiles: Sequence[TranslationProfile] | None = None,
        estimate_defaults: dict[str, float] | None = None,
    ) -> None:
        self._translation = translation
        self._tts = tts
        self._tts_engines = (
            dict(tts_engines) if tts_engines is not None else {default_tts_engine: tts}
        )
        self._translation_profiles = (
            list(translation_profiles) if translation_profiles is not None else []
        )
        self._estimate_defaults = (
            dict(estimate_defaults)
            if estimate_defaults is not None
            else _DEFAULT_ESTIMATE_DEFAULTS.copy()
        )

    async def get(self, language: str | None = None) -> Capabilities:
        await refresh_engine_voices(self._tts)
        languages = _intersection_languages(self._translation, self._tts)
        # Checks run concurrently; each provider bounds its own probe (VieNeu: 5 s).
        names = list(self._tts_engines)
        flags = await asyncio.gather(
            *(
                engine_available(p) if (p := self._tts_engines[n]) is not None else _no()
                for n in names
            )
        )
        infos: list[TtsEngineInfo] = []
        voices: list[Voice] = []
        for name, ok in zip(names, flags, strict=True):
            provider = self._tts_engines[name]
            label, kind, cost = _ENGINE_META.get(name, (name, "online", ""))
            infos.append(
                TtsEngineInfo(
                    id=name,
                    label=label,
                    kind=kind,
                    cost_label=cost,
                    available=ok,
                    languages=sorted(provider.languages()) if provider is not None else [],
                    # shortcut: engines without the attribute (fake) report 1.
                    max_concurrency=int(getattr(provider, "max_concurrency", 1)),
                )
            )
            if ok and provider is not None:
                found = (
                    _all_ordered_voices(provider)
                    if language is None
                    else list(provider.voices_for(language))
                )
                voices.extend(replace(v, engine=name) for v in found)
        return Capabilities(
            languages=languages,
            voices=voices,
            translation_profiles=list(self._translation_profiles),
            narrate_languages=sorted(self._tts.languages()),
            estimate_defaults=dict(self._estimate_defaults),
            tts_engines=infos,
        )


async def refresh_engine_voices(
    provider: TTSProvider, *, backoff_seconds: float | None = None
) -> None:
    """Let providers with remote voice lists (VieNeu) fetch them off the event loop.

    ``backoff_seconds=None`` keeps the provider's default (capabilities polling); user
    actions and retries pass 0 so a recovered sidecar is seen at once."""
    refresh = getattr(provider, "refresh_voices", None)
    if refresh is None:
        return
    if backoff_seconds is None:
        await refresh()
    else:
        await refresh(backoff_seconds=backoff_seconds)


async def _no() -> bool:
    return False


def _all_ordered_voices(tts: TTSProvider) -> list[Voice]:
    voices: list[Voice] = []
    seen: set[str] = set()
    for code in sorted(tts.languages()):
        for v in tts.voices_for(code):
            voices.append(v)
            seen.add(v.id)
    for v in tts.all_voices():
        if v.id not in seen:
            voices.append(v)
            seen.add(v.id)
    return voices


def _intersection_languages(translation: TranslationProvider, tts: TTSProvider) -> list[str]:
    result: list[str] = []
    for code in translation.supported_languages():
        if tts.voices_for(code):
            result.append(code)
    return result
