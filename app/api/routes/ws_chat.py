from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.graph.state import CompiledStateGraph

from app.domain.chat import ChatSession
from app.services.chat import (log_message, respond_with_ai, send_to_end_user,
                               send_to_support_agent, send_typing_to_end_user,
                               send_typing_to_support_agent)
from app.ws.auth import authenticate_socket
from app.ws.handlers import (conversation_has_lead, end_chat_session,
                             handle_form_capture, load_bot_prefs,
                             send_first_message_once)

logger = logging.getLogger(__name__)


router = APIRouter()

# In-memory map; swap for Redis later if you scale horizontally.
ACTIVE_SESSIONS: dict[str, ChatSession] = {}


@router.websocket("/chat/ws")
async def chat(websocket: WebSocket):
    await websocket.accept()
    metadata = await authenticate_socket(websocket, ACTIVE_SESSIONS)

    if metadata is None:
        return

    session, bot_id = metadata

    bot_pref = await load_bot_prefs(websocket, bot_id)
    if bot_pref is None:
        return

    first_message = bot_pref.get("first_message") or "Hi, How can I help you today?"
    greeting_key = f"greeting_sent:{session.conversation_id}"
    conversation_uuid = uuid.UUID(str(session.conversation_id))
    logger.info("Conversation UUID", extra={"conversation_uuid": conversation_uuid})

    form_required = (
        websocket == session.user_socket
        and not conversation_has_lead(conversation_uuid)
    )

    try:
        # Returning users already submitted the form. Skip it and only send the
        # greeting if this conversation has never received one.
        if websocket == session.user_socket and not form_required:
            await send_first_message_once(
                greeting_key,
                conversation_uuid,
                first_message,
                session,
            )

        while True:
            message_data = await websocket.receive_json()
            msg_type = message_data.get("type") if isinstance(message_data, dict) else None
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            elif msg_type == "end_chat":
                await end_chat_session(session)
                break

            logger.debug("WebSocket message received", extra={"type": msg_type})
            if msg_type == "form_capture":
                form_required = await handle_form_capture(
                    message_data,
                    form_required=form_required,
                    bot_id=bot_id,
                    session=session,
                    conversation_uuid=conversation_uuid,
                    greeting_key=greeting_key,
                    first_message=first_message,
                )
                continue

            # end user -> agent/ai
            if websocket == session.user_socket:
                if form_required:
                    await send_to_end_user(
                        {
                            "type": "form_required",
                            "message": "Please submit your details before starting the conversation.",
                            "role": "system",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue

                if msg_type == "typing":
                    is_typing = bool(message_data.get("is_typing", False))
                    await send_typing_to_support_agent(session, is_typing)
                    continue

                elif msg_type == "file":
                    file_key = message_data.get("message") or message_data.get("content")
                    if file_key:
                        await log_message(
                            session.conversation_id,
                            "user",
                            str(file_key),
                            "file",
                        )
                    await send_to_end_user(
                        {
                            "type": "message",
                            "message": "File has been uploaded, a support agent will review it and get back to you soon. If there is anything else I can help you with let me know.",
                            "role": "system",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue

                user_content = message_data.get("message") or message_data.get("content")
                if user_content:
                    await log_message(
                        session.conversation_id,
                        "user",
                        str(user_content),
                        "text",
                    )

                if session.mode == "human":
                    await send_typing_to_support_agent(session, False)
                    await send_to_support_agent(message_data, session)
                elif session.mode == "ai":
                    agent: CompiledStateGraph = websocket.app.state.agent
                    await respond_with_ai(message_data, bot_pref, session, agent)

            # agent -> user
            elif websocket == session.agent_socket:
                if msg_type == "typing":
                    is_typing = bool(message_data.get("is_typing", False))
                    await send_typing_to_end_user(session, is_typing)
                    continue
                # Clear any lingering agent typing indicator before the message.
                await send_typing_to_end_user(session, False)
                agent_message = {
                    **message_data,
                    "role": "support_agent",
                    "conversation_id": session.conversation_id,
                }
                await send_to_end_user(agent_message, session)

    except WebSocketDisconnect as e:
        if websocket == session.user_socket:
            await send_typing_to_support_agent(session, False)
            session.user_disconnect()
        elif websocket == session.agent_socket:
            await send_typing_to_end_user(session, False)
            session.agent_disconnect()
        logger.info("Chat session was disconnected", extra={"error": e})
    except Exception as e:
        logger.exception("Error in chat session", extra={"error": e})
    finally:
        await websocket.close()
