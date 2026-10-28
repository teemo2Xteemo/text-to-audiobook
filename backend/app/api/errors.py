import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.domain.errors import DomainError, ErrorType

logger = logging.getLogger(__name__)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def apply_security_headers[R: Response](response: R) -> R:
    """Attach the API security headers.

    Exception handlers run outside the HTTP middleware, so error responses
    must set the same headers themselves.
    """
    for name, value in _SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


_STATUS_BY_ERROR = {
    ErrorType.INVALID_INPUT: 400,
    ErrorType.UNSUPPORTED_LANGUAGE: 400,
    ErrorType.PROVIDER_RATE_LIMIT: 429,
    ErrorType.STORAGE_FAILED: 503,
    ErrorType.TIMEOUT: 504,
}


def envelope(error_type: ErrorType, message: str) -> dict[str, str]:
    return {"error_type": error_type.value, "message": message}


def domain_http_status(error_type: ErrorType) -> int:
    return _STATUS_BY_ERROR.get(error_type, 500)


async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
    response = JSONResponse(
        status_code=domain_http_status(exc.error_type),
        content=envelope(exc.error_type, exc.message),
    )
    return apply_security_headers(response)


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    errors = exc.errors() if hasattr(exc, "errors") else []  # type: ignore[no-untyped-call]
    response = JSONResponse(
        status_code=400,
        content=envelope(ErrorType.INVALID_INPUT, validation_message(list(errors))),
    )
    return apply_security_headers(response)


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict) and "error_type" in exc.detail:
        response = JSONResponse(status_code=exc.status_code, content=exc.detail)
        return apply_security_headers(response)
    message = exc.detail if isinstance(exc.detail, str) else "request failed"
    error_type = ErrorType.INVALID_INPUT if exc.status_code < 500 else ErrorType.INTERNAL_ERROR
    response = JSONResponse(
        status_code=exc.status_code,
        content=envelope(error_type, message),
    )
    return apply_security_headers(response)


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_server_error", exc_info=exc)
    response = JSONResponse(
        status_code=500,
        content=envelope(ErrorType.INTERNAL_ERROR, "internal server error"),
    )
    return apply_security_headers(response)


def validation_message(errors: list[Any]) -> str:
    if not errors:
        return "invalid request"
    first = errors[0]
    loc_parts = [str(part) for part in first.get("loc", ()) if part != "body"]
    loc = ".".join(loc_parts)
    msg = str(first.get("msg", "invalid request"))
    return f"{loc}: {msg}" if loc else msg
