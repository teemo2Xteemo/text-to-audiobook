from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_capabilities_service
from app.api.schemas import CapabilitiesResponse, VoiceResponse
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
    )
