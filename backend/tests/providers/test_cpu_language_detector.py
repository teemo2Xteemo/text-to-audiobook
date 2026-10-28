from __future__ import annotations

import asyncio

import pytest
from langdetect import DetectorFactory

from app.domain.errors import DomainError, ErrorType
from app.providers.language_detection.cpu import LANGDETECT_SEED, CpuLanguageDetector

ENGLISH = "The history of astronomy is a history of receding horizons in science."
CHINESE = "这是一段用于语言检测的中文句子，内容足够长以便识别。"
VIETNAMESE = "Đây là một câu tiếng Việt rõ ràng để nhận dạng ngôn ngữ nguồn."


def test_detects_english_as_en_us() -> None:
    detector = CpuLanguageDetector(min_confidence=0.5)
    result = asyncio.run(detector.detect(ENGLISH))
    assert result.language_code == "en-US"
    assert result.confidence >= 0.5
    assert DetectorFactory.seed == LANGDETECT_SEED


def test_detect_is_stable_with_fixed_seed() -> None:
    detector = CpuLanguageDetector()
    first = asyncio.run(detector.detect(ENGLISH))
    second = asyncio.run(detector.detect(ENGLISH))
    assert first.language_code == second.language_code == "en-US"
    assert DetectorFactory.seed == LANGDETECT_SEED


def test_detects_chinese_and_vietnamese_without_pairing_them() -> None:
    detector = CpuLanguageDetector()
    zh = asyncio.run(detector.detect(CHINESE))
    vi = asyncio.run(detector.detect(VIETNAMESE))
    assert zh.language_code == "zh-CN"
    assert vi.language_code == "vi-VN"


def test_garbage_text_does_not_assume_chinese() -> None:
    detector = CpuLanguageDetector()
    with pytest.raises(DomainError) as exc:
        asyncio.run(detector.detect("???"))
    assert exc.value.error_type is ErrorType.INVALID_INPUT
    assert "source_language" in exc.value.message
    assert "zh-CN" not in exc.value.message
    assert "Chinese" not in exc.value.message.lower()


def test_blank_text_asks_user_to_set_source() -> None:
    detector = CpuLanguageDetector()
    with pytest.raises(DomainError) as exc:
        asyncio.run(detector.detect("   "))
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_low_confidence_is_invalid_input(monkeypatch: pytest.MonkeyPatch) -> None:
    class Candidate:
        lang = "en"
        prob = 0.2

    monkeypatch.setattr(
        "app.providers.language_detection.cpu.detect_langs",
        lambda _text: [Candidate()],
    )
    detector = CpuLanguageDetector(min_confidence=0.5)
    with pytest.raises(DomainError) as exc:
        asyncio.run(detector.detect(ENGLISH))
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_unmapped_iso_asks_user_to_set_source(monkeypatch: pytest.MonkeyPatch) -> None:
    class Candidate:
        lang = "xyz"
        prob = 0.99

    monkeypatch.setattr(
        "app.providers.language_detection.cpu.detect_langs",
        lambda _text: [Candidate()],
    )
    detector = CpuLanguageDetector()
    with pytest.raises(DomainError) as exc:
        asyncio.run(detector.detect(ENGLISH))
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_detect_runs_langdetect_in_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    main_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    class Candidate:
        lang = "en"
        prob = 0.99

    def _fake_detect(text: str) -> list[Candidate]:
        del text
        seen.append(threading.current_thread())
        return [Candidate()]

    monkeypatch.setattr("app.providers.language_detection.cpu.detect_langs", _fake_detect)
    detector = CpuLanguageDetector()
    result = asyncio.run(detector.detect(ENGLISH))
    assert result.language_code == "en-US"
    assert len(seen) == 1
    assert seen[0] is not main_thread


def test_detect_candidates_returns_sorted_probabilities() -> None:
    detector = CpuLanguageDetector()
    candidates = asyncio.run(
        detector.detect_candidates(
            "Đây là một đoạn văn bản tiếng Việt mẫu để kiểm tra phát hiện ngôn ngữ."
        )
    )
    assert len(candidates) >= 1
    lang, conf = candidates[0]
    assert lang == "vi-VN"
    assert conf > 0.7


def test_detect_candidates_empty_and_whitespace_returns_empty_list() -> None:
    detector = CpuLanguageDetector()
    assert asyncio.run(detector.detect_candidates("")) == []
    assert asyncio.run(detector.detect_candidates("   \n\t  ")) == []


def test_detect_candidates_garbage_returns_empty_list() -> None:
    detector = CpuLanguageDetector()
    assert asyncio.run(detector.detect_candidates("???")) == []


def test_detect_candidates_sorts_descending_and_caps_at_top_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCandidate:
        def __init__(self, lang: str, prob: float) -> None:
            self.lang = lang
            self.prob = prob

    candidates = [
        FakeCandidate("es", 0.15),
        FakeCandidate("en", 0.40),
        FakeCandidate("fr", 0.25),
        FakeCandidate("de", 0.10),
        FakeCandidate("zh", 0.05),
        FakeCandidate("unknown_iso", 0.99),
        FakeCandidate("en", 0.30),
    ]

    monkeypatch.setattr(
        "app.providers.language_detection.cpu.detect_langs",
        lambda _text: candidates,
    )
    detector = CpuLanguageDetector()
    results = asyncio.run(detector.detect_candidates("some text"))
    assert len(results) == 3
    # en (0.40), fr (0.25), es (0.15)
    assert results[0] == ("en-US", 0.40)
    assert results[1] == ("fr-FR", 0.25)
    assert results[2] == ("es-ES", 0.15)


def test_detect_candidates_runs_in_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    main_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    class Candidate:
        lang = "en"
        prob = 0.99

    def _fake_detect(text: str) -> list[Candidate]:
        del text
        seen.append(threading.current_thread())
        return [Candidate()]

    monkeypatch.setattr("app.providers.language_detection.cpu.detect_langs", _fake_detect)
    detector = CpuLanguageDetector()
    results = asyncio.run(detector.detect_candidates(ENGLISH))
    assert results == [("en-US", 0.99)]
    assert len(seen) == 1
    assert seen[0] is not main_thread
