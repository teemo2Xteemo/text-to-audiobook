from __future__ import annotations

from datetime import UTC, datetime

from app.providers.http_retry import parse_retry_after


def test_retry_after_delta_is_capped_and_http_date_is_honored() -> None:
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("  120 ") == 60.0
    assert parse_retry_after("-5") == 0.0
    assert parse_retry_after("soon") is None
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    parsed = parse_retry_after("Tue, 27 Oct 2026 12:00:15 GMT", now=now)
    assert parsed is not None
    assert 14 <= parsed <= 16
