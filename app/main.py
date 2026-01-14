from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os
from app.logging_config import setup_logging
from app.api.middleware.jwt import verify_jwt_middleware
from app.api.router import api_router
from app.core.env import load_app_env
import logging

# load env and setup logging configuration
load_app_env()
setup_logging()
logger = logging.getLogger(__name__)

logger.info("Logger and env setup complete. Loading Environment", extra={"app_env": os.getenv("APP_ENV")})


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
logger.info("Fast API App started Successfully")

