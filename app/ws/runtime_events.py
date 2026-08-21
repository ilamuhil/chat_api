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
                if data.get("type") != "handover_timeout":
                    continue
                conversation_id = data["conversation_id"]
                session = ACTIVE_SESSIONS.get(conversation_id)
                if session:
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
