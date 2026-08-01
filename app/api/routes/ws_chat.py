from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select

from app.db.session import create_chat_db_session, create_dashboard_db_session
from app.domain.chat import ChatSession
from app.infra.redis_store import get_data, set_data
from app.models.chat_db_models import Messages
from app.models.dashboard_db_models import Bots, Leads
from app.services.chat import (log_message, respond_with_ai, send_to_end_user,
                               send_to_support_agent)
from app.ws.auth import authenticate_socket

logger = logging.getLogger(__name__)


router = APIRouter()

# In-memory map; swap for Redis later if you scale horizontally.
ACTIVE_SESSIONS: dict[str, ChatSession] = {}


async def send_first_message_once(
    greeting_key: str,
    conversation_uuid: uuid.UUID,
    first_message: str,
    session: ChatSession,
) -> None:
    if get_data(greeting_key) is True:
        return

    try:
        with create_chat_db_session() as chat_db:
            existing_message = chat_db.scalar(
                select(Messages.id)
                .where(
                    Messages.conversation_id == conversation_uuid,
                    Messages.role == "assistant",
                    Messages.content == first_message,
                )
                .limit(1)
            )
            if existing_message:
                set_data(greeting_key, True, ttl=None)
                return

            message = Messages(
                id=uuid.uuid4(),
                conversation_id=conversation_uuid,
                role="assistant",
                content_type="text",
                content=first_message,
                updated_at=datetime.now(),
            )
            chat_db.add(message)
            chat_db.commit()
    except Exception:
        logger.exception("Error logging first message to database")
        return

    await send_to_end_user(
    {
        "type": "typing",
        "from": "assistant",
        "is_typing": True,
        "conversation_id": session.conversation_id,
    },
    session,
)

    await send_to_end_user(
        {
            "type": "message",
            "message": first_message,
            "role": "assistant",
            "conversation_id": session.conversation_id,
        },
        session,
        persist=False,
    )
    set_data(greeting_key, True, ttl=None)
    await send_to_end_user(
        {
            "type": "typing",
            "from": "assistant",
            "is_typing": False,
            "conversation_id": session.conversation_id,
        },
        session,
    )


@router.websocket("/api/chat/ws")
async def chat(websocket: WebSocket):
    await websocket.accept()
    metadata = await authenticate_socket(websocket, ACTIVE_SESSIONS)
    
    if metadata is None:
        return

    session, bot_id = metadata
    
    
    
    bot_pref_raw = get_data(f"bot:{bot_id}:config")
    if not isinstance(bot_pref_raw, dict):
        with create_dashboard_db_session() as dashboard_db:
            bot = dashboard_db.scalar(select(Bots).where(Bots.id == bot_id))
            if bot is None:
                return
            bot_pref = {
                "first_message": bot.first_message,
                "confirmation_message": bot.confirmation_message,
                "lead_capture_message": bot.lead_capture_message,
                "lead_capture_timing": bot.lead_capture_timing,
                "capture_name": bot.capture_name,
                "capture_email": bot.capture_email,
                "capture_phone": bot.capture_phone,
            }
        set_data(f"bot:{bot_id}:config", bot_pref)
    else:
        bot_pref = bot_pref_raw

    first_message = bot_pref.get("first_message") or "Hi, How can I help you today?"
    greeting_key = f"greeting_sent:{session.conversation_id}"
    conversation_uuid = uuid.UUID(str(session.conversation_id))

    with create_dashboard_db_session() as dashboard_db:
        existing_lead = dashboard_db.scalar(
            select(Leads.id)
            .where(Leads.conversation_id == conversation_uuid)
            .limit(1)
        )
    
    form_required = websocket == session.user_socket and existing_lead is None

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

            logger.debug("WebSocket message received", extra={"type": msg_type})
            if msg_type == "form_capture":
                if not form_required:
                    await send_to_end_user(
                        {
                            "type": "form_capture",
                            "message": "Your details have already been submitted.",
                            "role": "system",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue

                try:
                    content = message_data.get("content")
                    if not isinstance(content, str):
                        raise ValueError("Form capture content must be a string")

                    fields = content.split(":", maxsplit=2)
                    if len(fields) != 3 or not all(field.strip() for field in fields):
                        raise ValueError("Form capture content must use name:email:phone")

                    name, email, phone = (field.strip() for field in fields)
                    lead = Leads(
                        id=uuid.uuid4(),
                        name=name,
                        email=email,
                        phone=phone,
                        bot_id=bot_id,
                        organization_id=session.organization_id,
                        conversation_id=conversation_uuid,
                        captured_at=datetime.now(),
                    )
                    with create_dashboard_db_session() as dashboard_db:
                        dashboard_db.add(lead)
                        dashboard_db.commit()
                    logger.info(
                        "Form capture data stored in database",
                        extra={"conversation_id": session.conversation_id},
                    )
                except Exception:
                    logger.exception("Error storing form capture data in database")
                    await send_to_end_user(
                        {
                            "type": "error",
                            "message": "Error storing form capture data to database",
                            "role": "system",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue

                form_required = False
                await send_to_end_user(
                    {
                        "type": "form_capture",
                        "message": "Thank you for submitting your details. This chat will continue via email if we get disconnected.",
                        "role": "system",
                        "conversation_id": session.conversation_id,
                    },
                    session,
                )
                await send_first_message_once(
                    greeting_key,
                    conversation_uuid,
                    first_message,
                    session,
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
                    await send_to_support_agent(
                        {
                            **message_data,
                            "type": "typing",
                            "from": "user",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
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
                    )

                if session.mode == "human":
                    await send_to_support_agent(message_data, session)
                elif session.mode == "ai":
                    agent:CompiledStateGraph  = websocket.app.state.agent
                    await respond_with_ai(message_data, session,agent)

            # agent -> user
            elif websocket == session.agent_socket:
                if msg_type == "typing":
                    await send_to_end_user(
                        {
                            **message_data,
                            "type": "typing",
                            "from": "agent",
                            "conversation_id": session.conversation_id,
                        },
                        session,
                    )
                    continue
                agent_message = {
                    **message_data,
                    "role": "assistant",
                    "conversation_id": session.conversation_id,
                }
                await send_to_end_user(agent_message, session)

    except WebSocketDisconnect as e:
        if websocket == session.user_socket:
            session.user_disconnect()
        elif websocket == session.agent_socket:
            session.agent_disconnect()
        logger.info("Chat session was disconnected", extra={"error": e})
    except Exception as e:
        logger.exception("Error in chat session",extra={"error":e})
    finally:
        await websocket.close()


