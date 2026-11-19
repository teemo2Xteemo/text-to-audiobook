import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.parse import _read_body_limited, _read_form
from app.config.settings import Settings
from app.domain.errors import DomainError, ErrorType
from app.main import create_app


def _http_scope(path: str, headers: list[tuple[bytes, bytes]]) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }


def _invoke(app, scope: dict, chunks: list[bytes]) -> tuple[int, dict, int]:
    pulled = 0

    async def receive():
        nonlocal pulled
        if pulled >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}
        piece = chunks[pulled]
        pulled += 1
        return {"type": "http.request", "body": piece, "more_body": pulled < len(chunks)}

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    raw = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return status, json.loads(raw), pulled


def test_connected_client_is_not_reported_disconnected(tmp_path: Path) -> None:
    from fastapi import Request
    from fastapi.responses import JSONResponse

    app = create_app(Settings(_env_file=None, storage_path=tmp_path, max_upload_bytes=200))

    @app.post("/api/disconnect-probe")
    async def probe(request: Request) -> JSONResponse:
        await request.body()
        return JSONResponse({"disconnected": await request.is_disconnected()})

    delivered = False
    hold = asyncio.Event()

    async def receive() -> dict:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b'{"text":"hi"}', "more_body": False}
        await hold.wait()
        return {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    scope = _http_scope("/api/disconnect-probe", [(b"content-type", b"application/json")])
    asyncio.run(app(scope, receive, send))
    status = next(item["status"] for item in sent if item["type"] == "http.response.start")
    raw = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    assert status == 200
    assert json.loads(raw)["disconnected"] is False


def test_chunked_json_stops_before_the_rest_of_the_body(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, storage_path=tmp_path, max_upload_bytes=200))
    chunks = [b"x" * 1024 for _ in range(40)]
    status, body, pulled = _invoke(
        app,
        _http_scope("/api/jobs", [(b"content-type", b"application/json")]),
        chunks,
    )
    assert status == 413
    assert body["error_type"] == ErrorType.PAYLOAD_TOO_LARGE
    assert "received more than" in body["message"]
    assert "limit 200 bytes" in body["message"]
    assert pulled < len(chunks)


def test_chunked_multipart_extra_file_part_is_rejected_early(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, storage_path=tmp_path, max_upload_bytes=200))
    header = (
        b"--bound\r\n"
        b'Content-Disposition: form-data; name="extra"; filename="extra.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n"
    )
    chunks = [header, *[b"x" * 1024 for _ in range(40)]]
    status, body, pulled = _invoke(
        app,
        _http_scope(
            "/api/jobs",
            [(b"content-type", b"multipart/form-data; boundary=bound")],
        ),
        chunks,
    )
    assert status == 413
    assert body["error_type"] == ErrorType.PAYLOAD_TOO_LARGE
    assert "received more than" in body["message"]
    assert pulled < len(chunks)


def test_read_body_limited_stops_pulling_chunks() -> None:
    class _Request:
        def __init__(self) -> None:
            self.pulled = 0

        async def stream(self):
            for _ in range(50):
                self.pulled += 1
                yield b"x" * 1000

    request = _Request()
    with pytest.raises(DomainError) as exc_info:
        asyncio.run(_read_body_limited(request, 2500, 200))  # type: ignore[arg-type]
    assert request.pulled == 3
    assert exc_info.value.error_type is ErrorType.PAYLOAD_TOO_LARGE
    assert "received more than 2500 bytes" in exc_info.value.message
    assert "limit 200 bytes" in exc_info.value.message


def test_read_form_maps_starlette_size_error_without_an_exact_count() -> None:
    class _Request:
        async def form(self, **kwargs: object):
            raise StarletteHTTPException(
                status_code=400, detail="Field exceeded maximum size of 64KB."
            )

    with pytest.raises(DomainError) as exc_info:
        asyncio.run(_read_form(_Request(), 200))  # type: ignore[arg-type]
    assert exc_info.value.error_type is ErrorType.PAYLOAD_TOO_LARGE
    assert "received more than" in exc_info.value.message
    assert "limit 200 bytes" in exc_info.value.message


def test_read_form_leaves_other_multipart_errors_alone() -> None:
    class _Request:
        async def form(self, **kwargs: object):
            raise StarletteHTTPException(
                status_code=400, detail="Too many files. Maximum number is 1."
            )

    with pytest.raises(StarletteHTTPException):
        asyncio.run(_read_form(_Request(), 200))  # type: ignore[arg-type]


def test_analyze_large_gb18030_keeps_health_responsive(tmp_path: Path) -> None:
    unit = "这是一段用于编码检测的正文。"
    sample = ("第一章 开篇\n" + unit).encode("gb18030")
    raw = sample * ((20 * 1024 * 1024) // len(sample) + 1)
    assert len(raw) >= 20 * 1024 * 1024
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")

    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    started = time.perf_counter()

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:

            async def analyze():
                return await client.post(
                    "/api/analyze",
                    files={"file": ("novel.txt", raw, "text/plain")},
                )

            analyze_task = asyncio.create_task(analyze())
            await asyncio.sleep(0.05)
            health_started = time.perf_counter()
            health = await client.get("/health")
            health_latency = time.perf_counter() - health_started
            health_finished = time.perf_counter()
            result = await analyze_task
            analyze_finished = time.perf_counter()
            return health, health_latency, health_finished, analyze_finished, result

    health, health_latency, health_finished, analyze_finished, result = asyncio.run(scenario())
    elapsed = time.perf_counter() - started
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    # Health returned while charset detection was still running, and quickly.
    assert health_finished < analyze_finished
    assert health_latency < 0.5, (
        f"health latency {health_latency:.3f}s during {elapsed:.3f}s analyze"
    )
    assert result.status_code == 200
    assert result.json()["encoding"].lower().startswith("gb")
