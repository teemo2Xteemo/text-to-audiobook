from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_capabilities_service
from app.api.schemas import CapabilitiesResponse, TranslationProfileResponse, VoiceResponse
from app.application.capabilities import CapabilitiesService

router = APIRouter()


@router.get("/api/capabilities", response_model=CapabilitiesResponse)
async def get_capabilities(
    language: str | None = Query(default=None),
    service: CapabilitiesService = Depends(get_capabilities_service),
) -> CapabilitiesResponse:
    filtered = language.strip() if language and language.strip() else None
    result = service.get(filtered)
    return CapabilitiesResponse(
        languages=result.languages,
        voices=[VoiceResponse.from_voice(voice) for voice in result.voices],
        translation_profiles=[
            TranslationProfileResponse(
                id=p.id,
                provider=p.provider,
                model=p.model,
                label=p.label,
                badge=p.badge,
                description=p.description,
                is_default=p.is_default,
                supported_languages=list(p.supported_languages),
            )
            for p in result.translation_profiles
        ],
        narrate_languages=result.narrate_languages,
        estimate_defaults=result.estimate_defaults,
    )
