import asyncio
import json
import logging

from app.infra.redis_client import get_async_redis
from app.services.chat import send_to_end_user
from app.ws.handover import receive_relay, refresh_session
from app.ws.registry import ACTIVE_SESSIONS

logger = logging.getLogger(__name__)


async def dispatch_runtime_event(data: dict) -> None:
    event_type = data.get("type")
    conversation_id = data.get("conversation_id")
    if not isinstance(conversation_id, str):
        return
    session = ACTIVE_SESSIONS.get(conversation_id)
    if session is None:
        return

    if event_type == "conversation_state":
        await refresh_session(session, notify=True)
        return
    if event_type == "socket_relay":
        await receive_relay(session, data)
        return

    if event_type == "conversation_closed":
        from app.ws.handlers import end_chat_session

        await end_chat_session(
            session,
            closed_by=data.get("closed_by") or "system",
            already_closed=True,
        )
        return

    # Every process receives broadcasts; only the visitor owner delivers them.
    if session.user_socket is None:
        return
    await refresh_session(session)
    if session.user_connection_id != session.active_user_connection_id:
        return
    if session.mode == "human" and (
        event_type == "handover_timeout"
        or (event_type == "assistance" and data.get("status") in {"searching", "busy"})
    ):
        return

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
        return

    if event_type != "handover_timeout":
        return
    await send_to_end_user(
        {
            "type": "message",
            "message": (
                "No counsellors are available at the moment.  Please try again later"
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


async def listen_for_runtime_events() -> None:
    backoff = 1
    while True:
        async_redis = await get_async_redis()
        pubsub = async_redis.pubsub()
        try:
            await pubsub.subscribe("chat_runtime_events")
            backoff = 1
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    await dispatch_runtime_event(json.loads(message["data"]))
                except Exception:
                    logger.exception("Could not dispatch conversation event")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Conversation event connection lost; reconnecting")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 10)
        finally:
            try:
                await pubsub.aclose()
                await async_redis.aclose()
            except Exception:
                logger.exception("Could not close conversation event connection")
