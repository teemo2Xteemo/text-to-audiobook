from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import domain_error_handler, http_exception_handler, validation_error_handler
from app.api.health import router as health_router
from app.api.jobs import router as jobs_router
from app.config import Settings
from app.domain.errors import DomainError


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Story to Audiobook", version="0.1.0")
    application.state.settings = settings or Settings()
    application.add_exception_handler(DomainError, domain_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(ValidationError, validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.include_router(health_router)
    application.include_router(jobs_router)
    return application


app = create_app()
