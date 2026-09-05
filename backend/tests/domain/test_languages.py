import asyncio

import pytest

from app.domain.errors import DomainError, ErrorType
from app.domain.languages import (
    AUTO_SOURCE_LANGUAGE,
    ensure_valid_languages,
    resolve_source_language,
)
from tests.fakes import FakeLanguageDetector


def test_detector_not_used_when_source_is_explicit() -> None:
    detector = FakeLanguageDetector()
    result = asyncio.run(resolve_source_language("你好", "zh-CN", detector))
    assert result == "zh-CN"
    assert detector.calls == []


def test_detector_used_when_source_is_auto() -> None:
    detector = FakeLanguageDetector(language_code="ko-KR", confidence=0.81)
    result = asyncio.run(resolve_source_language("story text", AUTO_SOURCE_LANGUAGE, detector))
    assert result == "ko-KR"
    assert detector.calls == ["story text"]


def test_auto_rejected_for_target() -> None:
    with pytest.raises(DomainError) as exc:
        ensure_valid_languages("en-US", AUTO_SOURCE_LANGUAGE)
    assert exc.value.error_type is ErrorType.INVALID_INPUT


def test_empty_languages_rejected() -> None:
    with pytest.raises(DomainError) as exc:
        ensure_valid_languages("  ", "vi-VN")
    assert exc.value.error_type is ErrorType.INVALID_INPUT
    with pytest.raises(DomainError):
        ensure_valid_languages("zh-CN", "")


def test_explicit_pair_is_valid_without_language_allowlist() -> None:
    ensure_valid_languages("zh-CN", "vi-VN")
    ensure_valid_languages("ja-JP", "en-US")
