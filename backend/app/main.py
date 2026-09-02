from fastapi import FastAPI

from app.api.health import router as health_router
from app.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="Story to Audiobook", version="0.1.0")
    application.state.settings = settings or Settings()
    application.include_router(health_router)
    return application


app = create_app()
