from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.api.capabilities import router as capabilities_router
from app.api.errors import (
    domain_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.config import Settings
from app.domain.errors import DomainError


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Story to Audiobook", version="0.1.0")
    application.state.settings = settings or Settings()

    @application.middleware("http")
    async def add_security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(ValidationError, validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
    application.include_router(health_router)
    application.include_router(capabilities_router)
    application.include_router(jobs_router)
    return application


app = create_app()
