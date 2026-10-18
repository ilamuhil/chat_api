"""Durable conversation ownership, fenced AI delivery, and socket relays."""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocketDisconnect
from sqlalchemy import select

from app.db.session import create_async_dashboard_db_session
from app.domain import ChatSession
from app.infra.redis_client import async_redis_client
from app.models.dashboard_db_models import ConversationsMeta
from app.services.latency import mark_response_sent
from app.ws.agent_tool_api import RUNTIME_EVENTS_CHANNEL, publish_to_channel

logger = logging.getLogger(__name__)
LEASE_SECONDS = 65


class HandoverRejected(ValueError):
    pass


class AITurnSuperseded(asyncio.CancelledError):
    """An older AI turn no longer owns the right to deliver output."""


@dataclass(frozen=True)
class AITurnScope:
    conversation_id: str
    organization_id: str
    version: int
    user_connection_id: str | None


current_ai_scope: ContextVar[AITurnScope | None] = ContextVar(
    "ai_turn_scope", default=None
)


def connection_key(connection_id: str) -> str:
    return f"chat:connection:{connection_id}"


async def touch_connection(connection_id: str | None) -> None:
    if connection_id:
        await async_redis_client.set(
            connection_key(connection_id), "1", ex=LEASE_SECONDS
        )


def _query(session: ChatSession):
    return select(ConversationsMeta).where(
        ConversationsMeta.id == uuid.UUID(session.conversation_id),
        ConversationsMeta.organization_id == session.organization_id,
    )


def apply_state(session: ChatSession, conversation: ConversationsMeta) -> None:
    version = conversation.handover_version
    if version != session.handover_version and session.ai_turn_task is not None:
        session.ai_turn_task.cancel()
    if conversation.mode == "human":
        session.mode = "human"
    elif conversation.mode == "ai":
        session.mode = "ai"
    else:
        raise HandoverRejected("Invalid conversation mode")
    session.handover_version = version
    session.active_user_connection_id = (
        str(conversation.visitor_connection_id)
        if conversation.visitor_connection_id
        else None
    )
    session.active_agent_connection_id = (
        str(conversation.agent_connection_id)
        if conversation.agent_connection_id
        else None
    )
    session.agent_user_id = (
        str(conversation.active_agent_id) if conversation.active_agent_id else None
    )


async def read_state(session: ChatSession):
    async with create_async_dashboard_db_session() as db:
        conversation = await db.scalar(_query(session))
        if conversation is None:
            raise HandoverRejected("Conversation not found")
        return conversation


async def state_payload(session: ChatSession) -> dict[str, Any]:
    connected = False
    if session.active_agent_connection_id:
        try:
            connected = bool(
                await async_redis_client.exists(
                    connection_key(session.active_agent_connection_id)
                )
            )
        except Exception:
            logger.exception("Could not check counsellor connection")
    return {
        "type": "conversation_mode",
        "conversation_id": session.conversation_id,
        "mode": session.mode,
        "version": session.handover_version,
        "agent_connected": connected,
    }


async def refresh_session(session: ChatSession, *, notify: bool = False):
    conversation = await read_state(session)
    apply_state(session, conversation)
    for socket, local_id, active_id in (
        (
            session.user_socket,
            session.user_connection_id,
            session.active_user_connection_id,
        ),
        (
            session.agent_socket,
            session.agent_connection_id,
            session.active_agent_connection_id,
        ),
    ):
        if socket is not None and local_id != active_id:
            try:
                await socket.close(code=4001, reason="Connection replaced")
            except (RuntimeError, WebSocketDisconnect):
                pass
    if notify:
        payload = await state_payload(session)
        for socket, local_id, active_id in (
            (
                session.user_socket,
                session.user_connection_id,
                session.active_user_connection_id,
            ),
            (
                session.agent_socket,
                session.agent_connection_id,
                session.active_agent_connection_id,
            ),
        ):
            if socket is not None and local_id == active_id:
                try:
                    await socket.send_json(payload)
                except (RuntimeError, WebSocketDisconnect):
                    pass
    return conversation


async def register_connection(
    session: ChatSession,
    *,
    role: str,
    connection_id: str,
    bot_id: uuid.UUID,
    user_id: str | None = None,
    takeover: bool = False,
    expected_version: int | None = None,
) -> None:
    async with create_async_dashboard_db_session() as db:
        conversation = await db.scalar(_query(session).with_for_update())
        if (
            conversation is None
            or conversation.bot_id != bot_id
            or conversation.status != "open"
        ):
            raise HandoverRejected("Conversation is unavailable")
        if role == "support_agent":
            same_owner = (
                conversation.mode == "human"
                and str(conversation.active_agent_id) == user_id
            )
            if (
                takeover
                and not same_owner
                and expected_version != conversation.handover_version
            ):
                raise HandoverRejected(
                    "Conversation changed. Join the conversation again."
                )
            if not takeover and conversation.mode != "human":
                raise HandoverRejected("Join the conversation before reconnecting")
            if (
                not takeover
                and conversation.active_agent_id is not None
                and str(conversation.active_agent_id) != user_id
            ):
                raise HandoverRejected(
                    "Another counsellor is handling this conversation"
                )
            conversation.mode = "human"
            conversation.handover_status = "accepted"
            conversation.active_agent_id = uuid.UUID(str(user_id))
            conversation.agent_connection_id = uuid.UUID(connection_id)
        else:
            conversation.visitor_connection_id = uuid.UUID(connection_id)
        # Redis must be available before claiming a connection on another process.
        await touch_connection(connection_id)
        conversation.handover_version += 1
        await db.commit()
        apply_state(session, conversation)
    await publish_to_channel(
        RUNTIME_EVENTS_CHANNEL,
        {
            "type": "conversation_state",
            "conversation_id": session.conversation_id,
        },
    )


async def unregister_connection(
    session: ChatSession, role: str, connection_id: str
) -> None:
    async with create_async_dashboard_db_session() as db:
        conversation = await db.scalar(_query(session).with_for_update())
        field = (
            "agent_connection_id"
            if role == "support_agent"
            else "visitor_connection_id"
        )
        if (
            conversation is not None
            and str(getattr(conversation, field)) == connection_id
        ):
            setattr(conversation, field, None)
            conversation.handover_version += 1
            # A network disconnect is not permission to restart the AI.
            await db.commit()
            apply_state(session, conversation)
    await async_redis_client.delete(connection_key(connection_id))
    await publish_to_channel(
        RUNTIME_EVENTS_CHANNEL,
        {
            "type": "conversation_state",
            "conversation_id": session.conversation_id,
        },
    )


@asynccontextmanager
async def guard_ai_output():
    scope = current_ai_scope.get()
    if scope is None:
        yield
        return
    async with create_async_dashboard_db_session() as db:
        conversation = await db.scalar(
            select(ConversationsMeta)
            .where(
                ConversationsMeta.id == uuid.UUID(scope.conversation_id),
                ConversationsMeta.organization_id == scope.organization_id,
            )
            .with_for_update()
        )
        if (
            conversation is None
            or conversation.status != "open"
            or conversation.mode != "ai"
            or conversation.handover_version != scope.version
            or str(conversation.visitor_connection_id) != scope.user_connection_id
        ):
            raise AITurnSuperseded()
        # Holding the same row lock as takeover orders delivery before its commit.
        yield


@asynccontextmanager
async def guard_agent_output(session: ChatSession, connection_id: str | None):
    if connection_id is None:
        yield
        return
    async with create_async_dashboard_db_session() as db:
        conversation = await db.scalar(_query(session).with_for_update())
        if (
            conversation is None
            or conversation.status != "open"
            or conversation.mode != "human"
            or str(conversation.agent_connection_id) != connection_id
        ):
            raise HandoverRejected("Counsellor connection was replaced")
        yield


async def relay_to(
    session: ChatSession,
    recipient: str,
    payload: dict[str, Any],
    *,
    sender_agent_connection_id: str | None = None,
) -> bool:
    # Unregistered sessions are used by isolated helpers/tests, never socket auth.
    if (
        session.user_connection_id is None
        and session.agent_connection_id is None
        and current_ai_scope.get() is None
    ):
        socket = session.user_socket if recipient == "user" else session.agent_socket
        if socket is None:
            return False
        try:
            await socket.send_json(payload)
            mark_response_sent(payload)
            return True
        except (RuntimeError, WebSocketDisconnect):
            return False
    guard = (
        guard_ai_output()
        if current_ai_scope.get() is not None
        else guard_agent_output(session, sender_agent_connection_id)
    )
    async with guard:
        conversation = await read_state(session)
        if conversation.status != "open":
            return False
        if sender_agent_connection_id and (
            conversation.mode != "human"
            or str(conversation.agent_connection_id) != sender_agent_connection_id
        ):
            raise HandoverRejected("Counsellor connection was replaced")
        field = (
            "visitor_connection_id" if recipient == "user" else "agent_connection_id"
        )
        target = getattr(conversation, field)
        if target is None:
            return False
        target = str(target)
        socket = session.user_socket if recipient == "user" else session.agent_socket
        local_id = (
            session.user_connection_id
            if recipient == "user"
            else session.agent_connection_id
        )
        if socket is not None and local_id == target:
            try:
                await socket.send_json(payload)
                mark_response_sent(payload)
                return True
            except (RuntimeError, WebSocketDisconnect):
                return False
        if not await async_redis_client.exists(connection_key(target)):
            return False
        published, subscribers = await publish_to_channel(
            RUNTIME_EVENTS_CHANNEL,
            {
                "type": "socket_relay",
                "conversation_id": session.conversation_id,
                "organization_id": session.organization_id,
                "recipient": recipient,
                "connection_id": target,
                "payload": payload,
                "sender_agent_connection_id": sender_agent_connection_id,
            },
        )
        return published and subscribers > 0


async def receive_relay(session: ChatSession, event: dict[str, Any]) -> None:
    if event.get("organization_id") != session.organization_id:
        return
    recipient = event.get("recipient")
    if recipient not in {"user", "agent"} or not isinstance(event.get("payload"), dict):
        return
    socket = session.user_socket if recipient == "user" else session.agent_socket
    local_id = (
        session.user_connection_id
        if recipient == "user"
        else session.agent_connection_id
    )
    if socket is None or event.get("connection_id") != local_id:
        return
    async with guard_agent_output(session, event.get("sender_agent_connection_id")):
        conversation = await read_state(session)
        field = (
            "visitor_connection_id" if recipient == "user" else "agent_connection_id"
        )
        if (
            conversation.status != "open"
            or str(getattr(conversation, field)) != local_id
        ):
            return
        await socket.send_json(event["payload"])
