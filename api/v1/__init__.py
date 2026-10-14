from fastapi import APIRouter
from .health import router as health_router
from .storage import router as storage_router
from .scripts import router as scripts_router
from .render import router as render_router
from .settings import router as settings_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router)
api_v1_router.include_router(storage_router)
api_v1_router.include_router(scripts_router)
api_v1_router.include_router(render_router)
api_v1_router.include_router(settings_router)

__all__ = ["api_v1_router"]
