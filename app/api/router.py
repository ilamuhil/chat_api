from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.training import router as training_router
from app.api.routes.ws_chat import router as ws_chat_router


api_router = APIRouter()
api_router.include_router(training_router)
api_router.include_router(ws_chat_router)


