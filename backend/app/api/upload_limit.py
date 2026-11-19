"""Bound request bodies before they are buffered without a limit.

Nginx allows a finite body above the default ``MAX_UPLOAD_BYTES``. This
middleware is the in-process backstop for chunked requests that omit
``Content-Length``, including multipart file parts Starlette would otherwise
spool with no size cap.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.responses import JSONResponse

from app.api.errors import apply_security_headers, envelope
from app.api.parse import MULTIPART_OVERHEAD, payload_too_large_message
from app.domain.errors import ErrorType

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

_LIMITED_METHODS = frozenset({"POST", "PUT", "PATCH"})


def _header(scope: Scope, name: bytes) -> bytes | None:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value
    return None


def _declared_length(scope: Scope) -> int | None:
    raw = _header(scope, b"content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _collect_body(receive: Receive, max_bytes: int) -> tuple[str, bytes]:
    """Read the ASGI body, stopping once it passes ``max_bytes``.

    Returns ``("ok", body)``, ``("too_big", b"")``, or ``("disconnected", b"")``.
    Chunks after the cap are not pulled.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        message = await receive()
        kind = message["type"]
        if kind == "http.disconnect":
            return "disconnected", b""
        if kind != "http.request":
            continue
        piece = message.get("body", b"") or b""
        total += len(piece)
        if total > max_bytes:
            return "too_big", b""
        if piece:
            chunks.append(piece)
        if not message.get("more_body", False):
            return "ok", b"".join(chunks)


class UploadSizeLimitMiddleware:
    """Reject ``/api/`` writes that exceed ``MAX_UPLOAD_BYTES`` plus framing."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope.get("method", "GET") not in _LIMITED_METHODS:
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        settings = scope["app"].state.settings
        limit = int(settings.max_upload_bytes)
        max_bytes = limit + MULTIPART_OVERHEAD
        declared = _declared_length(scope)
        if declared is not None and declared > max_bytes:
            await _send_too_large(send, scope, declared, limit, exact=True)
            return

        status, body = await _collect_body(receive, max_bytes)
        if status == "disconnected":
            return
        if status == "too_big":
            await _send_too_large(send, scope, max_bytes, limit, exact=False)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)


async def _send_too_large(
    send: Send, scope: Scope, received: int, limit: int, *, exact: bool
) -> None:
    response = JSONResponse(
        status_code=413,
        content=envelope(
            ErrorType.PAYLOAD_TOO_LARGE,
            payload_too_large_message(received, limit, exact=exact),
        ),
    )
    apply_security_headers(response)
    await response(scope, _empty_receive, send)


async def _empty_receive() -> Message:
    return {"type": "http.disconnect"}
