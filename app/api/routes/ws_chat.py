from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_chat_db, get_dashboard_db
from app.domain.chat import ChatSession
from app.infra.redis_store import get_data, set_data
from app.models.chat_db_models import Messages
from app.models.dashboard_db_models import Bots
from app.services.chat import (respond_with_ai, send_to_end_user,
                               send_to_support_agent)
from app.ws.auth import authenticate_socket

logger = logging.getLogger(__name__)


router = APIRouter()

# In-memory map; swap for Redis later if you scale horizontally.
ACTIVE_SESSIONS: dict[str, ChatSession] = {}


@router.websocket("/api/chat/ws")
async def chat(websocket: WebSocket, dashboard_db: Session = Depends(get_dashboard_db), chat_db: Session = Depends(get_chat_db)):
    await websocket.accept()
    metadata = await authenticate_socket(websocket, ACTIVE_SESSIONS)
    #get the data from the bot and store the metadata in redis store and then use it if necessary in the conversation flow. 
    
    if metadata is None:
        return

    session, bot_id = metadata
    
    bot_pref_raw = get_data(f"bot:{bot_id}:config")
    if not isinstance(bot_pref_raw, dict):
        stmnt = select(Bots).where(Bots.id == bot_id)
        bot = dashboard_db.scalar(stmnt)
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

    if get_data(greeting_key) is not True:
        existing_message = chat_db.scalar(
            select(Messages.id).where(Messages.conversation_id == conversation_uuid).limit(1)
        )
        if existing_message:
            set_data(greeting_key, True, ttl=None)
        else:
            try:
                message = Messages(
                    id=uuid.uuid4(),
                    conversation_id=conversation_uuid,
                    role="assistant",
                    content=first_message,
                    updated_at=datetime.now(),
                )
                chat_db.add(message)
                chat_db.commit()
                set_data(greeting_key, True, ttl=None)
                logger.info(f"Sending first message to end user: {first_message}")
                await send_to_end_user(
                    {
                        "type": "message",
                        "message": first_message,
                        "role": "assistant",
                        "conversation_id": session.conversation_id,
                    },
                    session,
                )
            except Exception as e:
                chat_db.rollback()
                logger.exception(f"Error logging first message to database: {e}")
          
    try:
        while True:
            message_data = await websocket.receive_json()
            msg_type = message_data.get("type") if isinstance(message_data, dict) else None

            # end user -> agent/ai
            if websocket == session.user_socket:
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
                await send_to_end_user(message_data, session)

    except WebSocketDisconnect:
        if websocket == session.user_socket:
            session.user_disconnect()
        elif websocket == session.agent_socket:
            session.agent_disconnect()
        print("Client disconnected")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await websocket.close()


