from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis

from app.core.jwt import verify_token

logger = logging.getLogger(__name__)

router = APIRouter()

redis = Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    decode_responses=True,
)

HEARTBEAT_SECONDS = 20


@router.get("/notifications/events")
async def get_notifications_events(
    token: str,
) -> StreamingResponse:
    claims = verify_token(
        token,
        {
            "require": [
                "exp",
                "iat",
                "iss",
                "aud",
                "sub",
                "organization_id",
                "type",
            ]
        },
    )

    if claims is None or claims.get("type") != "sse":
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
        )

    user_id = claims.get("sub")
    organization_id = claims.get("organization_id")

    if not user_id or not organization_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid SSE token claims",
        )

    channel = f"org_notifications:{organization_id}"
    pubsub = redis.pubsub()

    await pubsub.subscribe(channel)

    async def stream_notifications() -> AsyncGenerator[str, None]:
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=HEARTBEAT_SECONDS,
                )

                if message is None:
                    yield ": heartbeat\n\n"
                    continue

                try:
                    event = json.loads(message["data"])
                except (TypeError, json.JSONDecodeError):
                    logger.warning(
                        "Ignoring invalid notification payload",
                        extra={"channel": channel},
                    )
                    continue

                if event.get("user_id") != str(user_id):
                    continue

                event_id = event.get("id")

                if not event_id:
                    logger.warning(
                        "Ignoring notification without an ID",
                        extra={"channel": channel},
                    )
                    continue

                yield (
                    f"id: {event_id}\n"
                    "event: notification\n"
                    f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                )

        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Dashboard notification stream failed",
                extra={
                    "user_id": str(user_id),
                    "organization_id": str(organization_id),
                },
            )
        finally:
            try:
                await pubsub.unsubscribe(channel)
            finally:
                await pubsub.aclose()

    return StreamingResponse(
        stream_notifications(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
