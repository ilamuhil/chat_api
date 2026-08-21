from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select, update

from app.db.session import create_chat_db_session, create_dashboard_db_session
from app.domain.chat import ChatSession
from app.infra.redis_store import get_data, set_data
from app.models.chat_db_models import (
    BotConfigurations,
    EmbeddingConfigurations,
    Messages,
)
from app.models.dashboard_db_models import Bots, ConversationsMeta, Leads
from app.services.chat import (
    end_user_typing,
    send_to_end_user,
    send_typing_to_end_user,
    send_typing_to_support_agent,
)

logger = logging.getLogger(__name__)

BOT_PREFS_TTL_SECONDS = 5 * 60
REQUIRED_BOT_PREF_KEYS = {
    "bot_id",
    "embedding_configuration_id",
    "embedding_model",
    "embedding_dimension",
    "retrieval_k",
    "similarity_threshold",
}


async def send_first_message_once(
    greeting_key: str,
    conversation_uuid: uuid.UUID,
    first_message: str,
    session: ChatSession,
) -> None:
    if get_data(greeting_key) is True:
        return

    async with end_user_typing(session):
        try:
            with create_chat_db_session() as chat_db:
                existing_message = chat_db.scalar(
                    select(Messages.id)
                    .where(
                        Messages.conversation_id == conversation_uuid,
                        Messages.role == "system",
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
                    role="system",
                    content_type="text",
                    content=first_message,
                    updated_at=datetime.now(UTC),
                )
                chat_db.add(message)
                chat_db.commit()
        except Exception:
            logger.exception("Error logging first message to database")
            return
        await send_to_end_user(
            {
                "type": "message",
                "message": first_message,
                "role": "system",
                "conversation_id": session.conversation_id,
            },
            session,
            persist=False,
        )
        set_data(greeting_key, True, ttl=None)


async def load_bot_prefs(websocket: WebSocket, bot_id: Any) -> dict[str, Any] | None:
    bot_pref_raw = get_data(f"bot:{bot_id}:config")
    if isinstance(bot_pref_raw, dict) and REQUIRED_BOT_PREF_KEYS.issubset(bot_pref_raw):
        return bot_pref_raw

    with create_dashboard_db_session() as dashboard_db:
        bot = dashboard_db.scalar(select(Bots).where(Bots.id == bot_id))
        if bot is None:
            logger.error("Bot not found", extra={"bot_id": bot_id})
            await websocket.send_json({"type": "error", "message": "Bot Unavailable"})
            await websocket.close()
            return None

        bot_pref: dict[str, Any] = {
            "bot_id": str(bot.id),
            "first_message": bot.first_message,
            "name": bot.name,
            "institute_description": bot.business_description,
            "tone": bot.tone,
            "institute_name": bot.institute_name,
            "confirmation_message": bot.confirmation_message,
            "lead_capture_message": bot.lead_capture_message,
            "lead_capture_timing": bot.lead_capture_timing,
            "capture_name": bot.capture_name,
            "capture_email": bot.capture_email,
            "capture_phone": bot.capture_phone,
        }
        logger.info("Bot preferences set", extra={"bot_id": bot_id})

        with create_chat_db_session() as chat_db:
            embedding_config = chat_db.scalars(
                select(EmbeddingConfigurations)
                .where(
                    EmbeddingConfigurations.bot_id == bot_id,
                    EmbeddingConfigurations.state.in_(["active"]),
                )
                .order_by(EmbeddingConfigurations.created_at.desc())
            ).first()
            bot_config = (
                chat_db.scalars(
                    select(BotConfigurations)
                    .where(
                        BotConfigurations.bot_id == bot_id,
                        BotConfigurations.embedding_configuration_id
                        == embedding_config.id,
                        BotConfigurations.state.in_(["active"]),
                    )
                    .order_by(BotConfigurations.created_at.desc())
                ).first()
                if embedding_config is not None
                else None
            )
            if embedding_config is None or bot_config is None:
                logger.error(
                    "Model configuration not found",
                    extra={"bot_id": bot_id},
                )
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Could not retrieve required information. Please contact support.",
                    }
                )
                await websocket.close()
                return None

            bot_pref.update(
                {
                    "embedding_configuration_id": str(embedding_config.id),
                    "embedding_provider": embedding_config.provider,
                    "embedding_model": embedding_config.model,
                    "embedding_version": embedding_config.version,
                    "embedding_dimension": embedding_config.dimension,
                    "chunk_size": embedding_config.chunk_size,
                    "chunk_overlap": embedding_config.chunk_overlap,
                    "bot_configuration_id": str(bot_config.id),
                    "llm_provider": bot_config.provider,
                    "llm_model": bot_config.model,
                    "retrieval_k": bot_config.retrieval_k,
                    "similarity_threshold": bot_config.similarity_threshold,
                }
            )

    set_data(f"bot:{bot_id}:config", bot_pref, ttl=BOT_PREFS_TTL_SECONDS)
    return bot_pref


def conversation_has_lead(conversation_uuid: uuid.UUID) -> bool:
    with create_dashboard_db_session() as dashboard_db:
        existing_lead = dashboard_db.scalar(
            select(Leads.id).where(Leads.conversation_id == conversation_uuid).limit(1)
        )
    return existing_lead is not None


async def handle_form_capture(
    message_data: dict[str, Any],
    *,
    form_required: bool,
    bot_id: Any,
    session: ChatSession,
    conversation_uuid: uuid.UUID,
    greeting_key: str,
    first_message: str,
) -> bool:
    """Process a form_capture message. Returns the updated form_required flag."""
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
        return False

    try:
        content = message_data.get("content")
        if not isinstance(content, str):
            raise TypeError("Form capture content must be a string")

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
            captured_at=datetime.now(UTC),
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
        return True

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
    return False


async def end_chat_session(session: ChatSession) -> None:
    await send_typing_to_end_user(session, False)
    await send_typing_to_support_agent(session, False)
    with create_dashboard_db_session() as dashboard_db:
        dashboard_db.execute(
            update(ConversationsMeta)
            .where(
                ConversationsMeta.id == session.conversation_id,
            )
            .values(
                status="closed",
            )
        )
    if session.agent_socket:
        await session.agent_socket.send_json(
            {"type": "end_chat", "message": "Chat ended by user"}
        )
        await session.agent_socket.close()
    if session.user_socket:
        await session.user_socket.send_json(
            {"type": "end_chat", "message": "Chat ended"}
        )
        await session.user_socket.close()
