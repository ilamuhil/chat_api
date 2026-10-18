from __future__ import annotations

import logging
import uuid

from fastapi import WebSocket

from app.core.jwt import verify_token
from app.domain import ChatSession
from app.ws.agent_tool_api import get_conversation
from app.ws.handover import HandoverRejected, register_connection

logger = logging.getLogger(__name__)


async def authenticate_socket(
    websocket: WebSocket,
    active_sessions: dict[str, ChatSession],
) -> tuple[ChatSession, uuid.UUID] | None:
    data = await websocket.receive_json()
    if not isinstance(data, dict):
        await websocket.close(code=1008, reason="No data provided")
        return None

    token = data.get("token")
    conversation_id = data.get("conversation_id")
    visitor_id = data.get("visitor_id")

    if token is None or conversation_id is None:
        await websocket.close(code=1008, reason="No token or conversation id provided")
        return None

    claims = verify_token(
        token,
        {
            "require": [
                "exp",
                "iat",
                "aud",
                "iss",
                "conversation_id",
                "organization_id",
                "type",
            ]
        },
    )
    if claims is None:
        logger.error("Invalid token", extra={"conversation_id": conversation_id})
        await websocket.close(code=1008, reason="Invalid token")
        return None

    if claims.get("conversation_id") != conversation_id:
        logger.error(
            "Conversation mismatch",
            extra={
                "conversation_id": conversation_id,
                "expected": claims.get("conversation_id"),
            },
        )
        await websocket.close(code=1008, reason="Conversation mismatch")
        return None

    connection_type = claims.get("type")
    if connection_type == "user" and visitor_id is None:
        logger.error(
            "Visitor ID not found for user connection",
            extra={"conversation_id": conversation_id},
        )
        await websocket.close(code=1008, reason="Visitor ID not provided")
        return None

    organization_id = claims.get("organization_id")
    if organization_id is None:
        logger.error(
            "Organization ID not found", extra={"conversation_id": conversation_id}
        )
        await websocket.close(code=1008, reason="Organization ID not found")
        return None
    bot_id = claims.get("bot_id")
    if bot_id is None:
        logger.error("Bot ID not found", extra={"conversation_id": conversation_id})
        await websocket.close(code=1008, reason="Bot ID not found")
        return None
    try:
        bot_id = uuid.UUID(str(bot_id))
    except (ValueError, AttributeError, TypeError) as e:
        logger.error(
            "Invalid bot ID",
            extra={"conversation_id": conversation_id, "error": str(e)},
        )
        await websocket.close(code=1008, reason=f"Invalid bot ID: {e!s}")
        return None

    if connection_type == "user":
        conversation = await get_conversation(
            uuid.UUID(conversation_id), open_only=False
        )
        if (
            conversation is None
            or conversation.organization_id != organization_id
            or conversation.bot_id != bot_id
        ):
            await websocket.close(code=1008, reason="Conversation not found")
            return None
        if conversation.status == "closed":
            await websocket.send_json(
                {
                    "type": "end_chat",
                    "conversation_id": conversation_id,
                    "message": "Conversation closed",
                }
            )
            await websocket.close(code=1000)
            return None
        session = active_sessions.get(conversation_id)
        if session is not None:
            if session.organization_id != organization_id or session.visitor_id not in (
                None,
                visitor_id,
            ):
                await websocket.close(code=1008, reason="Visitor session mismatch")
                return None
            previous = session.user_socket
            if previous is not None and previous is not websocket:
                try:
                    await previous.close(code=4001, reason="Connection replaced")
                except RuntimeError:
                    pass
            session.user_connect(websocket)
        else:
            session = ChatSession(
                conversation_id=conversation_id,
                organization_id=organization_id,
                user_socket=websocket,
                visitor_id=visitor_id,
            )
            active_sessions[conversation_id] = session
        session.visitor_id = visitor_id
        connection_id = str(uuid.uuid4())
        session.user_connection_id = connection_id
        try:
            await register_connection(
                session,
                role="user",
                connection_id=connection_id,
                bot_id=bot_id,
            )
        except Exception:
            logger.exception("Could not register visitor connection")
            await websocket.close(code=1011, reason="Could not restore conversation")
            return None
        session.user_connection_id = connection_id
        websocket.state.chat_role = "user"
        websocket.state.connection_id = connection_id
        return session, bot_id

    if connection_type == "support_agent":
        user_id = claims.get("sub")
        try:
            uuid.UUID(str(user_id))
        except (ValueError, TypeError):
            await websocket.close(
                code=1008, reason="Agent identity is required; join again"
            )
            return None
        session = active_sessions.get(conversation_id)
        if session is not None and session.organization_id != organization_id:
            await websocket.close(code=1008, reason="Unauthorized organization access")
            return None
        if session is None:
            # The visitor may be connected to a different API process.
            session = ChatSession(
                conversation_id=conversation_id, organization_id=organization_id
            )
        connection_id = str(uuid.uuid4())
        try:
            await register_connection(
                session,
                role="support_agent",
                connection_id=connection_id,
                bot_id=bot_id,
                user_id=user_id,
                takeover=claims.get("agent_takeover") is True,
                expected_version=claims.get("handover_version"),
            )
        except HandoverRejected as error:
            await websocket.close(code=1008, reason=str(error))
            return None
        except Exception:
            logger.exception("Could not commit counsellor takeover")
            await websocket.close(code=1011, reason="Could not join conversation")
            return None
        previous = session.agent_socket
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=4001, reason="Connection replaced")
            except RuntimeError:
                pass
        session.agent_connect(websocket, user_id)
        session.agent_connection_id = connection_id
        active_sessions[conversation_id] = session
        websocket.state.chat_role = "support_agent"
        websocket.state.connection_id = connection_id
        return session, bot_id

    await websocket.close(code=1008, reason="Invalid type")
    return None
