import json
import logging

from app.infra.redis_client import get_async_redis
from app.services.chat import send_to_end_user
from app.ws.registry import ACTIVE_SESSIONS

logger = logging.getLogger(__name__)


async def listen_for_runtime_events() -> None:
    async_redis = await get_async_redis()
    pubsub = async_redis.pubsub()
    try:
        await pubsub.subscribe("chat_runtime_events")
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            try:
                data = json.loads(message["data"])
                event_type = data.get("type")
                conversation_id = data.get("conversation_id")
                if not isinstance(conversation_id, str):
                    continue
                session = ACTIVE_SESSIONS.get(conversation_id)
                if session is None:
                    continue

                if event_type == "assistance" and data.get("status") in {
                    "searching",
                    "connected",
                    "busy",
                }:
                    await send_to_end_user(
                        {
                            "type": "assistance",
                            "status": data["status"],
                            "conversation_id": conversation_id,
                        },
                        session,
                        persist=False,
                    )
                    continue

                if event_type != "handover_timeout":
                    continue
                await send_to_end_user(
                    {
                        "type": "message",
                        "message": (
                            "No counsellors are available at the moment. "
                            " Please try again later"
                        ),
                        "role": "system",
                        "conversation_id": conversation_id,
                    },
                    session,
                )
                await send_to_end_user(
                    {
                        "type": "assistance",
                        "status": "busy",
                        "conversation_id": conversation_id,
                    },
                    session,
                    persist=False,
                )
            except (json.JSONDecodeError, KeyError, TypeError):
                logger.error(
                    "Error in parsing runtime event message",
                    extra={"json_data": message.get("data")},
                )
    finally:
        try:
            await pubsub.unsubscribe("chat_runtime_events")
        finally:
            await pubsub.aclose()
            await async_redis.aclose()
