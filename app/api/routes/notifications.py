from __future__ import annotations

import json
import logging
import os
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


@router.get("/notifications/events", description="Listen to subscribed changes in redis pubsub and send it to dashboard client as an event stream for notifications")
async def get_notifications_events(
    token: str,
):
    claims = verify_token(token)
    if claims is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    org_id = claims.get("organization_id")  
    pubsub = redis.pubsub()
    org_notifications_channel = f"org_notifications:{org_id}"
    await pubsub.subscribe(org_notifications_channel)
    
    async def stream_notifications():
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue

                try:
                    event = json.loads(message["data"])
                except (TypeError, json.JSONDecodeError):
                    logger.warning(
                        "Ignoring invalid Redis notification payload",
                        extra={"channel": org_notifications_channel},
                    )
                    continue

                event_type = event.get("type")
                if event_type in {"handover_request", "handover_timeout"}:
                    yield (
                        f"event: {event_type}\n"
                        f"data: {json.dumps(event)}\n\n"
                    )
        except Exception:
            logger.exception(
                "Error streaming notifications",
                extra={"channel": org_notifications_channel},
            )
        finally:
            try:
                await pubsub.unsubscribe(org_notifications_channel)
            finally:
                await pubsub.aclose()
            logger.info(
                "Closed Redis notification subscription",
                extra={"channel": org_notifications_channel},
            )

    return StreamingResponse(stream_notifications(), media_type="text/event-stream")
