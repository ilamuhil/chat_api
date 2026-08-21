from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.edu_agent import initialize_agent
from app.api.middleware.jwt import verify_jwt_middleware
from app.api.router import api_router
from app.config.logging_config import setup_logging
from app.core.env import load_app_env
from app.ws.runtime_events import listen_for_runtime_events

# load env and setup logging configuration
load_app_env()
setup_logging()
logger = logging.getLogger(__name__)

logger.info(
    "Logger and env setup complete. Loading Environment",
    extra={"app_env": os.getenv("APP_ENV")},
)


# agent is now accessible as an attribute of the app
# app can be accessed as an attribute of request context (request.app.state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with initialize_agent() as agent:
        app.state.agent = agent
        runtime_events_task: asyncio.Task[None] | None = None
        try:
            runtime_events_task = asyncio.create_task(listen_for_runtime_events())
            yield
        except Exception as e:
            logger.exception(
                "Error in listening for runtime events", extra={"error": str(e)}
            )
            raise
        finally:
            if runtime_events_task is not None:
                runtime_events_task.cancel()
                with suppress(asyncio.CancelledError):
                    await runtime_events_task


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

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
