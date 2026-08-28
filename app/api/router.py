from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.messages import router as messages_router
from app.api.routes.model_config import router as model_config_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.training import router as training_router
from app.api.routes.ws_chat import router as ws_chat_router

api_router = APIRouter()
api_router.include_router(training_router, prefix="/api")
api_router.include_router(ws_chat_router, prefix="/api")
api_router.include_router(messages_router, prefix="/api")
api_router.include_router(notifications_router, prefix="/api")
api_router.include_router(model_config_router, prefix="/api")
