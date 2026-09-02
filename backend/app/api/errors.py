from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import DomainError, ErrorType

_STATUS_BY_ERROR = {
    ErrorType.INVALID_INPUT: 400,
    ErrorType.UNSUPPORTED_LANGUAGE: 400,
    ErrorType.STORAGE_FAILED: 503,
}


def envelope(error_type: ErrorType, message: str) -> dict[str, str]:
    return {"error_type": error_type.value, "message": message}


def domain_http_status(error_type: ErrorType) -> int:
    return _STATUS_BY_ERROR.get(error_type, 500)


async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=domain_http_status(exc.error_type),
        content=envelope(exc.error_type, exc.message),
    )


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    errors = exc.errors() if hasattr(exc, "errors") else []  # type: ignore[no-untyped-call]
    return JSONResponse(
        status_code=400,
        content=envelope(ErrorType.INVALID_INPUT, _validation_message(list(errors))),
    )


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error_type" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    message = exc.detail if isinstance(exc.detail, str) else "request failed"
    error_type = ErrorType.INVALID_INPUT if exc.status_code < 500 else ErrorType.STORAGE_FAILED
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope(error_type, message),
    )


def _validation_message(errors: list[Any]) -> str:
    if not errors:
        return "invalid request"
    first = errors[0]
    loc_parts = [str(part) for part in first.get("loc", ()) if part != "body"]
    loc = ".".join(loc_parts)
    msg = str(first.get("msg", "invalid request"))
    return f"{loc}: {msg}" if loc else msg
