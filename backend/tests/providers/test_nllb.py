from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.application.capabilities import CapabilitiesService
from app.domain.errors import DomainError, ErrorType
from app.domain.languages import AUTO_SOURCE_LANGUAGE
from app.providers.translation.nllb import (
    NllbTranslationProvider,
    TransformersNllbEngine,
    bcp47_to_flores,
)
from app.providers.tts.fake import FakeTTSProvider

NLLB_PATH = Path(__file__).resolve().parents[2] / "app" / "providers" / "translation" / "nllb.py"


class FakeNllbEngine:
    def __init__(self, *, max_tokens: int = 512, token_count: int | None = None) -> None:
        self._max_tokens = max_tokens
        self._token_count = token_count
        self.generate_calls: list[tuple[str, str, str]] = []
        self.fail: Exception | None = None

    def max_input_tokens(self) -> int:
        return self._max_tokens

    def count_tokens(self, text: str, source_flores: str) -> int:
        del source_flores
        if self._token_count is not None:
            return self._token_count
        return max(1, len(text))

    def generate(self, text: str, source_flores: str, target_flores: str) -> str:
        self.generate_calls.append((text, source_flores, target_flores))
        if self.fail is not None:
            raise self.fail
        return f"{target_flores}:{text}"


def test_bcp47_mapping_covers_demo_and_another_pair() -> None:
    assert bcp47_to_flores("zh-CN") == "zho_Hans"
    assert bcp47_to_flores("vi-VN") == "vie_Latn"
    assert bcp47_to_flores("ja-JP") == "jpn_Jpan"
    assert bcp47_to_flores("en-US") == "eng_Latn"
    assert bcp47_to_flores("ko-KR") == "kor_Hang"
    provider = NllbTranslationProvider(model_id="test-model", engine=FakeNllbEngine())
    supported = set(provider.supported_languages())
    assert {"zh-CN", "vi-VN"} < supported
    assert AUTO_SOURCE_LANGUAGE not in supported


def test_nllb_fake_tts_capabilities_are_not_a_single_pair(tmp_path: Path) -> None:
    translation = NllbTranslationProvider(model_id="test-model", engine=FakeNllbEngine())
    tts = FakeTTSProvider(output_dir=tmp_path)
    languages = CapabilitiesService(translation=translation, tts=tts).get().languages
    assert "zh-CN" in languages
    assert "vi-VN" in languages
    assert "ja-JP" in languages
    assert "en-US" in languages
    assert "ko-KR" in languages


def test_unsupported_bcp47_is_unsupported_language() -> None:
    with pytest.raises(DomainError) as exc:
        bcp47_to_flores("xx-XX")
    assert exc.value.error_type is ErrorType.UNSUPPORTED_LANGUAGE


def test_translate_maps_bcp47_inside_adapter() -> None:
    engine = FakeNllbEngine()
    provider = NllbTranslationProvider(model_id="test-model", engine=engine)
    result = asyncio.run(provider.translate("hello", "zh-CN", "vi-VN"))
    assert result == "vie_Latn:hello"
    assert engine.generate_calls == [("hello", "zho_Hans", "vie_Latn")]


def test_translate_second_pair_is_not_zh_vi() -> None:
    engine = FakeNllbEngine()
    provider = NllbTranslationProvider(model_id="test-model", engine=engine)
    result = asyncio.run(provider.translate("hello", "ja-JP", "en-US"))
    assert result == "eng_Latn:hello"
    assert engine.generate_calls == [("hello", "jpn_Jpan", "eng_Latn")]


def test_translate_rejects_auto_source() -> None:
    engine = FakeNllbEngine()
    provider = NllbTranslationProvider(model_id="test-model", engine=engine)
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.translate("hello", AUTO_SOURCE_LANGUAGE, "en-US"))
    assert exc.value.error_type is ErrorType.UNSUPPORTED_LANGUAGE
    assert engine.generate_calls == []


def test_translate_rejects_unmapped_language_before_engine() -> None:
    engine = FakeNllbEngine()
    provider = NllbTranslationProvider(model_id="test-model", engine=engine)
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.translate("hello", "xx-XX", "en-US"))
    assert exc.value.error_type is ErrorType.UNSUPPORTED_LANGUAGE
    assert engine.generate_calls == []


def test_overlong_token_sequence_fails_without_truncation() -> None:
    engine = FakeNllbEngine(max_tokens=8, token_count=32)
    provider = NllbTranslationProvider(model_id="test-model", engine=engine)
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.translate("hello", "en-US", "ja-JP"))
    assert exc.value.error_type is ErrorType.TRANSLATION_FAILED
    assert "token" in exc.value.message
    assert engine.generate_calls == []


def test_engine_runtime_error_is_translation_failed() -> None:
    engine = FakeNllbEngine()
    engine.fail = RuntimeError("backend exploded")
    provider = NllbTranslationProvider(model_id="test-model", engine=engine)
    with pytest.raises(DomainError) as exc:
        asyncio.run(provider.translate("hello", "en-US", "ja-JP"))
    assert exc.value.error_type is ErrorType.TRANSLATION_FAILED


def test_nllb_module_has_no_top_level_torch_import() -> None:
    tree = ast.parse(NLLB_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"torch", "transformers"})


def test_nllb_module_does_not_use_language_detector() -> None:
    tree = ast.parse(NLLB_PATH.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    used_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
                imported_modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
            imported_modules.add(node.module)
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            used_names.add(node.id)
    assert imported_roots.isdisjoint({"langdetect"})
    assert all("language_detection" not in module.split(".") for module in imported_modules)
    assert imported_names.isdisjoint({"LanguageDetector", "CpuLanguageDetector"})
    assert used_names.isdisjoint({"LanguageDetector", "CpuLanguageDetector"})


class _FakeTensor:
    shape = (1, 4)

    def to(self, _device: object) -> _FakeTensor:
        return self


class _RecordingTokenizer:
    def __init__(self) -> None:
        self.src_lang: str | None = None
        self.calls: list[dict[str, object]] = []

    def __call__(self, text: str, **kwargs: object) -> dict[str, _FakeTensor]:
        self.calls.append({"text": text, **kwargs})
        return {"input_ids": _FakeTensor(), "attention_mask": _FakeTensor()}

    def convert_tokens_to_ids(self, _token: str) -> int:
        return 2

    def batch_decode(self, _generated: object, skip_special_tokens: bool = True) -> list[str]:
        del skip_special_tokens
        return ["ok"]


class _FakeSeq2SeqModel:
    config = type("Config", (), {"max_position_embeddings": 1024})()

    def generate(self, **kwargs: object) -> list[str]:
        del kwargs
        return ["tokens"]


def test_transformers_engine_tokenizes_with_truncation_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = TransformersNllbEngine("unused-id")
    tokenizer = _RecordingTokenizer()
    engine._tokenizer = tokenizer
    engine._model = _FakeSeq2SeqModel()
    engine._device = "cpu"
    engine._ensure_loaded = lambda: None  # type: ignore[method-assign]

    assert engine.count_tokens("hello", "eng_Latn") == 4

    class _NoGrad:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *args: object) -> bool:
            del args
            return False

    fake_torch = ModuleType("torch")
    fake_torch.no_grad = lambda: _NoGrad()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert engine.generate("hello", "eng_Latn", "vie_Latn") == "ok"
    assert len(tokenizer.calls) == 2
    for call in tokenizer.calls:
        assert call["truncation"] is False
        assert call["return_tensors"] == "pt"
        assert call["text"] == "hello"


@pytest.mark.integration
def test_nllb_live_translate_opt_in() -> None:
    if os.environ.get("NLLB_INTEGRATION") != "1":
        pytest.skip("set NLLB_INTEGRATION=1 to download and run distilled NLLB")
    pytest.importorskip("torch")
    pytest.importorskip("transformers")

    model_id = os.environ.get("NLLB_MODEL_ID", "facebook/nllb-200-distilled-600M")
    provider = NllbTranslationProvider(model_id=model_id)
    translated = asyncio.run(provider.translate("Hello.", "en-US", "vi-VN"))
    assert translated.strip()
    second = asyncio.run(provider.translate("Hello.", "en-US", "ja-JP"))
    assert second.strip()
    assert translated != second
