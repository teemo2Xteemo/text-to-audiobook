from __future__ import annotations

import pytest

from app.providers.tts.brackets import sanitize_brackets


@pytest.mark.parametrize(
    "text,expected",
    [
        ("a [x] b", "a (x) b"),
        ("a 【x】 b", "a (x) b"),
        ("a 〔x〕 b", "a (x) b"),
        ("a [ ] b", "a b"),
        ("[ ]", ""),
        ("【】", ""),
        ("  many   \n spaces ", "many spaces"),
    ],
)
def test_sanitize_brackets(text: str, expected: str) -> None:
    assert sanitize_brackets(text) == expected
