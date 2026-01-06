from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware.jwt import verify_jwt_middleware
from app.api.router import api_router
from app.core.env import load_app_env


# Load env early (before importing modules that may read environment variables)
load_app_env()


def create_app() -> FastAPI:
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.middleware("http")(verify_jwt_middleware)
    app.include_router(api_router)
    return app


app = create_app()


