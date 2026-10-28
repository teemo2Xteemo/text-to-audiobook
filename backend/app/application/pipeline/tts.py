from __future__ import annotations

from app.domain.audio import AudioArtifact, TTSSettings, Voice
from app.domain.errors import DomainError, ErrorType
from app.domain.languages import FAMILY_PREFERENCES
from app.domain.ports import TTSProvider

_DIALECT_KEYWORDS = ("liaoning", "shaanxi", "shandong", "sichuan")


def _normalize_lang(tag: str) -> str:
    return tag.strip().replace("_", "-").lower()


def _is_cantonese(tag_or_id: str) -> bool:
    norm = _normalize_lang(tag_or_id)
    return (
        norm.startswith("zh-hk")
        or norm.startswith("zh-mo")
        or norm.startswith("zh-yue")
        or norm.startswith("yue")
        or "cantonese" in norm
    )


def _is_mandarin(tag_or_id: str) -> bool:
    norm = _normalize_lang(tag_or_id)
    if _is_cantonese(norm):
        return False
    subtag = norm.split("-")[0]
    return subtag == "zh" or norm.startswith("cmn") or "mandarin" in norm


def _is_cross_cantonese_mandarin(lang: str, candidate_lang_or_id: str) -> bool:
    if _is_cantonese(lang) and _is_mandarin(candidate_lang_or_id):
        return True
    if _is_mandarin(lang) and _is_cantonese(candidate_lang_or_id):
        return True
    return False


def _is_dialect_voice(voice_or_lang: str) -> bool:
    norm = _normalize_lang(voice_or_lang)
    return any(d in norm for d in _DIALECT_KEYWORDS)


def _get_all_voices(tts: TTSProvider) -> list[Voice]:
    return list(tts.all_voices())


def _available_languages(tts: TTSProvider) -> set[str]:
    langs = tts.languages()
    if langs:
        return set(langs)
    return {v.language for v in _get_all_voices(tts)}


def resolve_voice(tts: TTSProvider, language: str, voice: str | None = None) -> str:
    if voice:
        exact_voices = list(tts.voices_for(language))
        exact_match = next((v for v in exact_voices if v.id == voice), None)
        if exact_match is not None:
            if _is_cross_cantonese_mandarin(
                language, exact_match.language
            ) or _is_cross_cantonese_mandarin(language, exact_match.id):
                raise DomainError(
                    ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language"
                )
            return exact_match.id

        all_voices = _get_all_voices(tts)
        matching_voice = next((v for v in all_voices if v.id == voice), None)
        if matching_voice is None:
            raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language")

        if _is_cross_cantonese_mandarin(
            language, matching_voice.language
        ) or _is_cross_cantonese_mandarin(language, matching_voice.id):
            raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language")

        lang_primary = _normalize_lang(language).split("-")[0]
        voice_primary = _normalize_lang(matching_voice.language).split("-")[0]
        if lang_primary != voice_primary:
            raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "voice is not available for language")

        return matching_voice.id

    exact_voices = list(tts.voices_for(language))
    for v in exact_voices:
        if _is_cross_cantonese_mandarin(language, v.language) or _is_cross_cantonese_mandarin(
            language, v.id
        ):
            continue
        return v.id

    primary = _normalize_lang(language).split("-")[0]
    available_langs = _available_languages(tts)

    candidate_langs: list[str] = []
    for cand in available_langs:
        cand_norm = _normalize_lang(cand)
        if cand_norm.split("-")[0] != primary:
            continue
        if _is_cross_cantonese_mandarin(language, cand):
            continue
        if _is_mandarin(language) and _is_dialect_voice(cand):
            continue
        candidate_langs.append(cand)

    prefs = (
        FAMILY_PREFERENCES.get(_normalize_lang(language)) or FAMILY_PREFERENCES.get(primary) or []
    )
    pref_order = {lang.lower(): i for i, lang in enumerate(prefs)}

    def sort_key(cand: str) -> tuple[int, str]:
        c_lower = cand.lower()
        return (pref_order.get(c_lower, 999), c_lower)

    candidate_langs.sort(key=sort_key)

    for cand in candidate_langs:
        candidate_voices = list(tts.voices_for(cand))
        for v in candidate_voices:
            if _is_cross_cantonese_mandarin(language, v.language) or _is_cross_cantonese_mandarin(
                language, v.id
            ):
                continue
            if _is_mandarin(language) and _is_dialect_voice(v.id):
                continue
            return v.id

    for v in _get_all_voices(tts):
        v_primary = _normalize_lang(v.language).split("-")[0]
        if v_primary != primary:
            continue
        if _is_cross_cantonese_mandarin(language, v.language) or _is_cross_cantonese_mandarin(
            language, v.id
        ):
            continue
        if _is_mandarin(language) and (_is_dialect_voice(v.language) or _is_dialect_voice(v.id)):
            continue
        return v.id

    raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "no voice available for language")


def select_voice(provider: TTSProvider, language: str, voice: str | None = None) -> str:
    return resolve_voice(provider, language, voice)


async def synthesize_chunk(
    text: str,
    *,
    language: str,
    voice: str | None,
    settings: TTSSettings,
    provider: TTSProvider,
) -> AudioArtifact:
    selected = select_voice(provider, language, voice)
    try:
        return await provider.synthesize(text, language, selected, settings)
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError(ErrorType.TTS_FAILED, "tts failed") from exc
