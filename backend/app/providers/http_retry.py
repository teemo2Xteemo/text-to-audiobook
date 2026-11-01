from __future__ import annotations

import urllib.error
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

MAX_RETRY_AFTER_SECONDS = 60.0


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        seconds = _retry_after_http_date(text, now=now)
        if seconds is None:
            return None
    if seconds < 0:
        return 0.0
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def _retry_after_http_date(value: str, *, now: datetime | None) -> float | None:
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    clock = now or datetime.now(UTC)
    return (when - clock).total_seconds()


def retry_after_header(exc: urllib.error.HTTPError) -> str | None:
    headers = getattr(exc, "headers", None)
    if headers is None:
        return None
    for name in (
        "Retry-After",
        "retry-after",
        "x-ratelimit-reset-tokens",
        "x-ratelimit-reset-requests",
    ):
        value = headers.get(name)
        if value is not None:
            raw = str(value).strip()
            if raw.endswith("s") and not raw.endswith("ms"):
                raw = raw[:-1].strip()
            return raw
    ms_val = headers.get("retry-after-ms")
    if ms_val is not None:
        try:
            return str(float(ms_val) / 1000.0)
        except ValueError:
            pass
    return None
