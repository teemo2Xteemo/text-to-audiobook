from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.main import create_app


def test_unhandled_exception_is_internal_error_with_security_headers(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))

    @app.get("/__test_boom")
    async def _boom() -> None:
        raise RuntimeError("disk path /secret/should-not-leak")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__test_boom")

    assert response.status_code == 500
    assert response.json() == {
        "error_type": "INTERNAL_ERROR",
        "message": "internal server error",
    }
    assert "/secret/" not in response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_http_500_without_envelope_is_internal_error(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))

    @app.get("/__test_http_500")
    async def _http_500() -> None:
        raise HTTPException(status_code=500, detail="boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__test_http_500")

    assert response.status_code == 500
    assert response.json()["error_type"] == "INTERNAL_ERROR"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_client_error_response_carries_security_headers(tmp_path: Path) -> None:
    app = create_app(Settings(_env_file=None, storage_path=tmp_path))
    with TestClient(app) as client:
        response = client.get("/api/jobs/not-a-uuid")
    assert response.status_code == 400
    assert response.json()["error_type"] == "INVALID_INPUT"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
