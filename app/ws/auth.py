from __future__ import annotations

import uuid

from fastapi import WebSocket

from app.core.jwt import verify_token
from app.domain.chat import ChatSession


async def authenticate_socket(
    websocket: WebSocket,
    active_sessions: dict[str, ChatSession],
) -> tuple[ChatSession, uuid.UUID] | None:
    data = await websocket.receive_json()
    if data is None:
        await websocket.close(code=1008, reason="No data provided")
        return None

    token = data.get("token")
    conversation_id = data.get("conversation_id")

    if token is None or conversation_id is None:
        await websocket.close(code=1008, reason="No token/conversation id provided")
        return None

    claims = verify_token(
        token,
        {"require": ["exp", "iat", "aud", "iss", "conversation_id", "organization_id", "type"]},
    )
    if claims is None:
        await websocket.close(code=1008, reason="Invalid token")
        return None

    if claims.get("conversation_id") != conversation_id:
        await websocket.close(code=1008, reason="Conversation mismatch")
        return None
    
    organization_id = claims.get("organization_id") 
    if organization_id is None:
        await websocket.close(code=1008, reason="Organization ID not found")
        return None
    bot_id = claims.get("bot_id")
    if bot_id is None:
        await websocket.close(code=1008, reason="Bot ID not found")
        return None
    try:
        bot_id = uuid.UUID(str(bot_id))
    except Exception as e:
        await websocket.close(code=1008, reason=f"Invalid bot ID: {str(e)}")
        return None

    if claims.get("type") == "user":
        session = ChatSession(
            conversation_id=conversation_id,
            organization_id=organization_id,
            user_socket=websocket,
        )
        active_sessions[conversation_id] = session
        return session,bot_id
    

    if claims.get("type") == "agent":
        session = active_sessions.get(conversation_id)
        if session is None:
            await websocket.close(code=1008, reason="Session not found")
            return None

        if session.organization_id != claims.get("organization_id"):
            await websocket.close(code=1008, reason="Organization mismatch")
            return None

        session.agent_connect(websocket)
        return session,bot_id

    await websocket.close(code=1008, reason="Invalid type")
    return None


