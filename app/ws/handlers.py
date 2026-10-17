from __future__ import annotations

import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from starlette.websockets import WebSocketState

from app.db.session import (
    create_async_chat_db_session,
    create_async_dashboard_db_session,
)
from app.domain import ChatSession
from app.infra.redis_store import async_get_data, async_set_data
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
from app.services.conversation_lifecycle import close_conversation
from app.services.lead_capture import capture_lead_form, parse_lead_form
from app.services.notifications import (
    create_notifications,
    publish_notifications,
)

logger = logging.getLogger(__name__)

BOT_PREFS_TTL_SECONDS = 5 * 60
REQUIRED_BOT_PREF_KEYS = {
    "capture_leads",
    "bot_id",
    "bot_configuration_id",
    "embedding_configuration_id",
    "embedding_model",
    "embedding_dimension",
    "llm_model",
    "retrieval_k",
    "similarity_threshold",
}


async def send_first_message_once(
    greeting_key: str,
    conversation_uuid: uuid.UUID,
    first_message: str,
    session: ChatSession,
) -> None:
    if await async_get_data(greeting_key) is True:
        return

    async with end_user_typing(session):
        try:
            async with create_async_chat_db_session() as chat_db:
                existing_message = await chat_db.scalar(
                    select(Messages.id)
                    .where(
                        Messages.conversation_id == conversation_uuid,
                        Messages.role == "system",
                        Messages.content == first_message,
                    )
                    .limit(1)
                )
                if existing_message:
                    await async_set_data(greeting_key, True, ttl=None)
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
                await chat_db.commit()
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
        await async_set_data(greeting_key, True, ttl=None)


async def associate_lead_with_conversation(
    conversation_uuid: uuid.UUID,
    lead_id: uuid.UUID,
) -> bool:
    try:
        async with create_async_dashboard_db_session() as dashboard_db:
            result = cast(
                CursorResult[Any],
                await dashboard_db.execute(
                    update(ConversationsMeta)
                    .where(ConversationsMeta.id == conversation_uuid)
                    .values(lead_id=lead_id),
                ),
            )
            await dashboard_db.commit()
            return result.rowcount == 1
    except Exception:
        logger.exception(
            "Error associating lead with conversation",
            extra={"conversation_uuid": conversation_uuid, "lead_id": lead_id},
        )
        return False


async def load_bot_prefs(websocket: WebSocket, bot_id: Any) -> dict[str, Any] | None:
    bot_pref_raw = await async_get_data(f"bot:{bot_id}:config")
    if isinstance(bot_pref_raw, dict) and REQUIRED_BOT_PREF_KEYS.issubset(bot_pref_raw):
        return bot_pref_raw

    async with create_async_dashboard_db_session() as dashboard_db:
        bot = await dashboard_db.scalar(select(Bots).where(Bots.id == bot_id))
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
            "capture_leads": bot.capture_leads,
            "capture_name": bot.capture_name,
            "capture_email": bot.capture_email,
            "capture_phone": bot.capture_phone,
        }
        logger.info("Bot preferences set", extra={"bot_id": bot_id})

        async with create_async_chat_db_session() as chat_db:
            embedding_config = (
                await chat_db.scalars(
                    select(EmbeddingConfigurations)
                    .where(
                        EmbeddingConfigurations.bot_id == bot_id,
                        EmbeddingConfigurations.state.in_(["active"]),
                    )
                    .order_by(EmbeddingConfigurations.created_at.desc())
                )
            ).first()
            bot_config = (
                (
                    await chat_db.scalars(
                        select(BotConfigurations)
                        .where(
                            BotConfigurations.bot_id == bot_id,
                            BotConfigurations.embedding_configuration_id
                            == embedding_config.id,
                            BotConfigurations.state.in_(["active"]),
                        )
                        .order_by(BotConfigurations.created_at.desc())
                    )
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
                    "min_chunk_tokens": embedding_config.min_chunk_tokens,
                    "target_chunk_tokens": embedding_config.target_chunk_tokens,
                    "max_chunk_tokens": embedding_config.max_chunk_tokens,
                    "bot_configuration_id": str(bot_config.id),
                    "llm_provider": bot_config.provider,
                    "llm_model": bot_config.model,
                    "retrieval_k": bot_config.retrieval_k,
                    "similarity_threshold": bot_config.similarity_threshold,
                }
            )

    await async_set_data(f"bot:{bot_id}:config", bot_pref, ttl=BOT_PREFS_TTL_SECONDS)
    return bot_pref


async def has_lead(
    visitor_uuid: uuid.UUID,
    bot_id: uuid.UUID,
    organization_id: str,
) -> tuple[Literal[True], uuid.UUID] | tuple[Literal[False], None]:
    async with create_async_dashboard_db_session() as dashboard_db:
        existing_lead = await dashboard_db.scalar(
            select(Leads.id)
            .where(
                Leads.visitor_id == visitor_uuid,
                Leads.bot_id == bot_id,
                Leads.organization_id == organization_id,
                Leads.deleted_at.is_(None),
                Leads.email.is_not(None),
                Leads.email != "",
            )
            .order_by(Leads.captured_at.desc())
            .limit(1)
        )
    if existing_lead is not None:
        return True, uuid.UUID(str(existing_lead))
    logger.info(
        "No lead found",
        extra={
            "visitor_uuid": visitor_uuid,
            "bot_id": bot_id,
            "organization_id": organization_id,
        },
    )
    return False, None


async def handle_form_capture(
    message_data: dict[str, Any],
    *,
    form_required: bool,
    bot_id: Any,
    session: ChatSession,
    conversation_uuid: uuid.UUID,
    greeting_key: str,
    first_message: str,
    visitor_uuid: uuid.UUID,
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
        profile = parse_lead_form(message_data.get("content"))
        lead_id = await capture_lead_form(
            session, uuid.UUID(str(bot_id)), visitor_uuid, profile
        )
        logger.info(
            "Form capture data stored in database",
            extra={"conversation_id": session.conversation_id},
        )
        try:
            lead_notifications = await create_notifications(
                organization_id=session.organization_id,
                notification_type="lead_captured",
                title="New lead captured",
                body="A new visitor lead has been captured.",
                metadata={
                    "leadId": str(lead_id),
                    "conversationId": str(conversation_uuid),
                    "botId": str(bot_id),
                },
            )

            await publish_notifications(
                organization_id=session.organization_id,
                notifications=lead_notifications,
            )
        except Exception:
            # Lead capture itself succeeded. The lead still appears on
            # the dashboard even if notification creation fails.
            logger.exception(
                "Failed to create lead capture notifications",
                extra={
                    "lead_id": str(lead_id),
                    "conversation_id": str(conversation_uuid),
                },
            )
    except Exception:
        logger.exception("Error storing form capture data in database")
        await send_to_end_user(
            {
                "type": "error",
                "message": "User information could not be captured. Please contact support.",
                "role": "system",
                "conversation_id": session.conversation_id,
            },
            session,
        )
        return True

    await send_to_end_user(
        {
            "type": "form_capture",
            "message": "Your details have been saved for the institute." if message_data.get("fallback") == "contact" else "Thank you for submitting your details. You can start your conversation now.",
            "fallback": message_data.get("fallback"),
            "role": "system",
            "conversation_id": session.conversation_id,
        },
        session,
    )
    if message_data.get("fallback") != "contact":
        await send_first_message_once(
            greeting_key, conversation_uuid, first_message, session,
        )
    return False


def _agent_end_chat_message(closed_by: str | None) -> str:
    if closed_by in {"support_agent", "agent", "admin"}:
        return "Chat ended by support_agent"
    if closed_by in {"visitor", "user"}:
        return "Chat ended by user"
    return "Chat ended"


async def end_chat_session(
    session: ChatSession, closed_by: str | None = "system", already_closed: bool = False
) -> None:
    await send_typing_to_end_user(session, False)
    await send_typing_to_support_agent(session, False)
    if not already_closed:
        await close_conversation(
            uuid.UUID(session.conversation_id),
            closed_by=closed_by or "system",
            organization_id=session.organization_id,
        )
    for socket in (session.agent_socket, session.user_socket):
        if (
            socket is None
            or socket.application_state != WebSocketState.CONNECTED
            or socket.client_state != WebSocketState.CONNECTED
        ):
            continue
        with suppress(RuntimeError, WebSocketDisconnect):
            await socket.send_json(
                {
                    "type": "end_chat",
                    "closed_by": closed_by,
                    "role": "system",
                    "message": _agent_end_chat_message(closed_by),
                    "conversation_id": session.conversation_id,
                }
            )
            await socket.close(code=1000, reason="This conversation is closed")


async def request_end_chat(session: ChatSession, websocket: WebSocket) -> bool:
    """Return True after closure; visitor requests never close a live agent chat."""
    if websocket == session.agent_socket:
        await end_chat_session(session, closed_by="support_agent")
        return True
    if websocket != session.user_socket:
        return False
    agent = session.agent_socket
    if (
        agent is not None
        and agent.application_state == WebSocketState.CONNECTED
        and agent.client_state == WebSocketState.CONNECTED
    ):
        if not session.end_chat_requested:
            if not session.agent_user_id:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "The agent needs to rejoin before receiving an end-chat request.",
                    }
                )
                return False
            notifications = await create_notifications(
                organization_id=session.organization_id,
                notification_type="end_chat_request",
                title="Visitor requested to end chat",
                body="The visitor has asked you to close the conversation.",
                metadata={"conversationId": session.conversation_id},
                recipient_user_ids=[uuid.UUID(session.agent_user_id)],
            )
            if not notifications:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "The agent could not be notified. Please try again.",
                    }
                )
                return False
            session.end_chat_requested = True
            await publish_notifications(
                organization_id=session.organization_id, notifications=notifications
            )
            await agent.send_json(
                {
                    "type": "end_chat_request",
                    "role": "system",
                    "message": "The visitor has requested to end this conversation.",
                    "conversation_id": session.conversation_id,
                }
            )
        await websocket.send_json(
            {
                "type": "end_chat_requested",
                "message": "agent has been notified to end chat",
                "conversation_id": session.conversation_id,
            }
        )
        return False
    await end_chat_session(session, closed_by="visitor")
    return True
