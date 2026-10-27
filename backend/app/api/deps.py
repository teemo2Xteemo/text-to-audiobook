from fastapi import Request

from app.application.capabilities import CapabilitiesService
from app.application.jobs import JobService
from app.config.factory import (
    build_capabilities_service,
    build_job_service,
    build_language_detector,
    build_tts_provider,
)
from app.config.settings import Settings
from app.domain.ports import LanguageDetector, TTSProvider


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_job_service(request: Request) -> JobService:
    existing = getattr(request.app.state, "job_service", None)
    if existing is not None:
        return existing
    created = build_job_service(request.app.state.settings)
    request.app.state.job_service = created
    return created


def get_capabilities_service(request: Request) -> CapabilitiesService:
    existing = getattr(request.app.state, "capabilities_service", None)
    if existing is not None:
        return existing
    created = build_capabilities_service(request.app.state.settings)
    request.app.state.capabilities_service = created
    return created


def get_language_detector(request: Request) -> LanguageDetector:
    existing = getattr(request.app.state, "language_detector", None)
    if existing is not None:
        return existing
    created = build_language_detector(request.app.state.settings)
    request.app.state.language_detector = created
    return created


def get_tts_provider(request: Request) -> TTSProvider:
    existing = getattr(request.app.state, "tts_provider", None)
    if existing is not None:
        return existing
    created = build_tts_provider(request.app.state.settings)
    request.app.state.tts_provider = created
    return created
