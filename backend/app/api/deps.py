from fastapi import Request

from app.application.jobs import JobService
from app.config.factory import build_job_service
from app.config.settings import Settings


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_job_service(request: Request) -> JobService:
    existing = getattr(request.app.state, "job_service", None)
    if existing is not None:
        return existing
    return build_job_service(request.app.state.settings)
