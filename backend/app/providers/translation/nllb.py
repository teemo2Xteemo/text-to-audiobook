from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import AUTO_SOURCE_LANGUAGE

logger = logging.getLogger(__name__)

# Domain BCP-47 → FLORES-200. Vendor codes stay in this adapter.
_BCP47_TO_FLORES: dict[str, str] = {
    "ar-SA": "arb_Arab",
    "bn-BD": "ben_Beng",
    "cs-CZ": "ces_Latn",
    "da-DK": "dan_Latn",
    "de-DE": "deu_Latn",
    "el-GR": "ell_Grek",
    "en-GB": "eng_Latn",
    "en-US": "eng_Latn",
    "es-ES": "spa_Latn",
    "es-MX": "spa_Latn",
    "fa-IR": "pes_Arab",
    "fi-FI": "fin_Latn",
    "fr-FR": "fra_Latn",
    "he-IL": "heb_Hebr",
    "hi-IN": "hin_Deva",
    "hu-HU": "hun_Latn",
    "id-ID": "ind_Latn",
    "it-IT": "ita_Latn",
    "ja-JP": "jpn_Jpan",
    "ko-KR": "kor_Hang",
    "ms-MY": "zsm_Latn",
    "nb-NO": "nob_Latn",
    "nl-NL": "nld_Latn",
    "pl-PL": "pol_Latn",
    "pt-BR": "por_Latn",
    "pt-PT": "por_Latn",
    "ro-RO": "ron_Latn",
    "ru-RU": "rus_Cyrl",
    "sv-SE": "swe_Latn",
    "sw-KE": "swh_Latn",
    "ta-IN": "tam_Taml",
    "th-TH": "tha_Thai",
    "tr-TR": "tur_Latn",
    "uk-UA": "ukr_Cyrl",
    "ur-PK": "urd_Arab",
    "vi-VN": "vie_Latn",
    "zh-CN": "zho_Hans",
    "zh-Hans": "zho_Hans",
    "zh-HK": "zho_Hant",
    "zh-Hant": "zho_Hant",
    "zh-SG": "zho_Hans",
    "zh-TW": "zho_Hant",
}

_PLACEHOLDER_MAX_LENGTH = 100_000
_DEFAULT_MAX_INPUT_TOKENS = 1024


class NllbEngine(Protocol):
    def max_input_tokens(self) -> int: ...

    def count_tokens(self, text: str, source_flores: str) -> int: ...

    def generate(self, text: str, source_flores: str, target_flores: str) -> str: ...


def bcp47_to_flores(language: str) -> str:
    flores = _BCP47_TO_FLORES.get(language)
    if flores is None:
        raise DomainError(ErrorType.UNSUPPORTED_LANGUAGE, "language is not supported")
    return flores


class NllbTranslationProvider:
    """CPU NLLB adapter registered as ``TRANSLATION_PROVIDER=nllb``."""

    def __init__(self, model_id: str, *, engine: NllbEngine | None = None) -> None:
        self._model_id = model_id
        self._engine = engine

    def supported_languages(self) -> Sequence[str]:
        return list(_BCP47_TO_FLORES)

    async def translate(self, text: str, source_language: str, target_language: str) -> str:
        if source_language == AUTO_SOURCE_LANGUAGE:
            raise DomainError(
                ErrorType.UNSUPPORTED_LANGUAGE,
                "auto is not a translation source; resolve it first",
            )
        source_flores = bcp47_to_flores(source_language)
        target_flores = bcp47_to_flores(target_language)
        engine = self._get_engine()
        token_count = engine.count_tokens(text, source_flores)
        if token_count > engine.max_input_tokens():
            raise DomainError(ErrorType.TRANSLATION_FAILED, "text exceeds model token limit")
        logger.info(
            "nllb_translate",
            extra={
                "provider": "nllb",
                "model": self._model_id,
                "source_language": source_language,
                "target_language": target_language,
                "character_count": len(text),
            },
        )
        try:
            return engine.generate(text, source_flores, target_flores)
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(ErrorType.TRANSLATION_FAILED, "translation failed") from exc

    def _get_engine(self) -> NllbEngine:
        if self._engine is None:
            self._engine = TransformersNllbEngine(self._model_id)
        return self._engine


class TransformersNllbEngine:
    """Loads NLLB on CPU. ``transformers`` / ``torch`` are imported lazily."""

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self._tokenizer = None
        self._model = None
        self._device = None

    def max_input_tokens(self) -> int:
        self._ensure_loaded()
        configured = getattr(self._model.config, "max_position_embeddings", None)
        if isinstance(configured, int) and 0 < configured < _PLACEHOLDER_MAX_LENGTH:
            return configured
        tokenizer_max = getattr(self._tokenizer, "model_max_length", None)
        if isinstance(tokenizer_max, int) and 0 < tokenizer_max < _PLACEHOLDER_MAX_LENGTH:
            return tokenizer_max
        return _DEFAULT_MAX_INPUT_TOKENS

    def count_tokens(self, text: str, source_flores: str) -> int:
        self._ensure_loaded()
        self._tokenizer.src_lang = source_flores
        encoded = self._tokenizer(text, truncation=False, return_tensors="pt")
        return int(encoded["input_ids"].shape[-1])

    def generate(self, text: str, source_flores: str, target_flores: str) -> str:
        import torch

        self._ensure_loaded()
        self._tokenizer.src_lang = source_flores
        encoded = self._tokenizer(text, truncation=False, return_tensors="pt")
        encoded = {key: value.to(self._device) for key, value in encoded.items()}
        forced_bos_token_id = self._tokenizer.convert_tokens_to_ids(target_flores)
        with torch.no_grad():
            generated = self._model.generate(
                **encoded,
                forced_bos_token_id=forced_bos_token_id,
                max_new_tokens=self.max_input_tokens(),
            )
        return str(self._tokenizer.batch_decode(generated, skip_special_tokens=True)[0])

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        self._device = torch.device("cpu")
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_id)
        self._model = AutoModelForSeq2SeqLM.from_pretrained(self._model_id)
        self._model.to(self._device)
        self._model.train(False)
