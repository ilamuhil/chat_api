from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.graph.state import CompiledStateGraph
from starlette.websockets import WebSocketState

from app.services.chat import (
    log_message,
    respond_with_ai,
    send_to_end_user,
    send_to_support_agent,
    send_typing_to_end_user,
    send_typing_to_support_agent,
)
from app.ws.agent_tool_api import get_conversation
from app.ws.auth import authenticate_socket
from app.ws.handlers import (
    associate_lead_with_conversation,
    end_chat_session,
    handle_form_capture,
    has_lead,
    load_bot_prefs,
    send_first_message_once,
)
from app.ws.registry import ACTIVE_SESSIONS

logger = logging.getLogger(__name__)


router = APIRouter()

# In-memory map; swap for Redis later if you scale horizontally.


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

    visitor_uuid: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    form_required = False
    if websocket == session.user_socket:
        try:
            visitor_uuid = uuid.UUID(str(session.visitor_id))
        except (ValueError, AttributeError, TypeError):
            logger.exception(
                "Error parsing visitor UUID", extra={"visitor_id": session.visitor_id}
            )
            return
        lead_exists, lead_id = has_lead(
            visitor_uuid,
            bot_id,
            session.organization_id,
        )
        form_required = not lead_exists
    ai_queue: asyncio.Queue[dict[str, Any]] | None = None
    ai_worker_task: asyncio.Task[None] | None = None

    if websocket == session.user_socket:
        ai_queue = asyncio.Queue()

        async def process_ai_queue() -> None:
            while True:
                queued_message = await ai_queue.get()
                try:
                    agent: CompiledStateGraph = websocket.app.state.agent
                    await respond_with_ai(
                        queued_message,
                        bot_pref,
                        session,
                        agent,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Error processing queued AI message",
                        extra={"conversation_id": session.conversation_id},
                    )
                finally:
                    ai_queue.task_done()

        ai_worker_task = asyncio.create_task(process_ai_queue())

    try:
        # Returning users already submitted the form. Skip it and only send the
        # greeting if this conversation has never received one.
        if websocket == session.user_socket and not form_required:
            if lead_id is not None:
                await associate_lead_with_conversation(conversation_uuid, lead_id)
            await send_first_message_once(
                greeting_key,
                conversation_uuid,
                first_message,
                session,
            )

        while True:
            # if conversation is closed disconnect the websocket
            try:
                conversation = await get_conversation(
                    conversation_uuid, open_only=False
                )
                if conversation is not None and conversation.status == "closed":
                    await end_chat_session(
                        session, closed_by="system", already_closed=True
                    )
                    break
            except Exception:
                logger.exception(
                    "Could not find an open conversation",
                    extra={"conversation_id": conversation_uuid},
                )
                break
            message_data = await websocket.receive_json()
            msg_type = (
                message_data.get("type") if isinstance(message_data, dict) else None
            )
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            elif msg_type == "end_chat":
                await end_chat_session(session)
                break

            logger.debug("WebSocket message received", extra={"type": msg_type})
            if msg_type == "form_capture":
                if websocket != session.user_socket or visitor_uuid is None:
                    continue
                form_required = await handle_form_capture(
                    message_data,
                    form_required=form_required,
                    bot_id=bot_id,
                    session=session,
                    conversation_uuid=conversation_uuid,
                    greeting_key=greeting_key,
                    first_message=first_message,
                    visitor_uuid=visitor_uuid,
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
                    file_key = message_data.get("message") or message_data.get(
                        "content"
                    )
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

                user_content = message_data.get("message") or message_data.get(
                    "content"
                )
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
                    if ai_queue is not None:
                        await ai_queue.put(message_data)

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
        if ai_worker_task is not None:
            ai_worker_task.cancel()
            try:
                await ai_worker_task
            except asyncio.CancelledError:
                pass
        if websocket.application_state != WebSocketState.DISCONNECTED:
            await websocket.close()
