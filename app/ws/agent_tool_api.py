from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, cast

from redis.exceptions import RedisError
from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult

from app.db.session import create_dashboard_db_session
from app.infra.redis_client import redis_client
from app.models.dashboard_db_models import ConversationsMeta

logger = logging.getLogger(__name__)


def _request_handover_sync(
    conversation_id: uuid.UUID,
) -> bool:
    with create_dashboard_db_session() as session:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ConversationsMeta)
                .where(
                    ConversationsMeta.id == conversation_id,
                    ConversationsMeta.status == "open",
                    or_(
                        ConversationsMeta.handover_status == "none",
                        ConversationsMeta.handover_status.is_(None),
                    ),
                )
                .values(handover_status="requested")
            ),
        )

        if result.rowcount != 1:
            session.rollback()
            return False

        session.commit()
        return True


async def request_conversation_handover(
    conversation_id: uuid.UUID,
) -> bool:
    try:
        return await asyncio.to_thread(
            _request_handover_sync,
            conversation_id,
        )
    except Exception:
        logger.exception(
            "Failed to request conversation handover",
            extra={"conversation_id": str(conversation_id)},
        )
        return False


def _get_conversation_sync(
    conversation_id: uuid.UUID,
    *,
    open_only: bool = True,
) -> ConversationsMeta | None:
    with create_dashboard_db_session() as session:
        statement = select(ConversationsMeta).where(
            ConversationsMeta.id == conversation_id
        )
        if open_only:
            statement = statement.where(ConversationsMeta.status == "open")
        return session.execute(statement).scalar_one_or_none()


async def get_conversation(
    conversation_id: uuid.UUID, open_only: bool = True
) -> ConversationsMeta | None:
    return await asyncio.to_thread(
        _get_conversation_sync, conversation_id, open_only=open_only
    )


def _publish_to_channel_sync(
    channel_key: str,
    payload: str,
) -> tuple[bool, int]:
    try:
        subscriber_count = redis_client.publish(channel_key, payload)
        if type(subscriber_count) is not int:
            raise TypeError("Redis PUBLISH did not return an integer")
        return True, subscriber_count
    except RedisError:
        logger.exception(
            "Error publishing to Redis channel",
            extra={"channel_key": channel_key},
        )
        return False, 0


async def publish_to_channel(
    channel_key: str,
    data: dict[str, Any],
) -> tuple[bool, int]:
    try:
        payload = json.dumps(data)
    except (TypeError, ValueError):
        logger.exception(
            "Error serializing Redis event",
            extra={"channel_key": channel_key},
        )
        return False, 0

    return await asyncio.to_thread(
        _publish_to_channel_sync,
        channel_key,
        payload,
    )


def _update_handover_status_sync(
    conversation_id: uuid.UUID,
    status: str,
) -> bool:
    with create_dashboard_db_session() as session:
        statement = update(ConversationsMeta).where(
            ConversationsMeta.id == conversation_id,
            ConversationsMeta.status == "open",
        )
        result = cast(
            CursorResult[Any],
            session.execute(statement.values(handover_status=status)),
        )
        if result.rowcount != 1:
            session.rollback()
            return False
        session.commit()
        return True


def _timeout_handover_status_sync(conversation_id: uuid.UUID) -> bool:
    with create_dashboard_db_session() as session:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(ConversationsMeta)
                .where(
                    ConversationsMeta.id == conversation_id,
                    ConversationsMeta.status == "open",
                    ConversationsMeta.handover_status == "requested",
                )
                .values(handover_status="timed_out")
            ),
        )
        if result.rowcount != 1:
            session.rollback()
            return False
        session.commit()
        return True


async def update_conversation_handover_status(
    conversation_id: uuid.UUID,
    status: str,
) -> bool:
    try:
        return await asyncio.to_thread(
            _update_handover_status_sync,
            conversation_id,
            status,
        )
    except Exception:
        logger.exception(
            "Error updating conversation handover status",
            extra={
                "conversation_id": str(conversation_id),
                "status": status,
            },
        )
        return False


def agent_handover_timeout_handler(conversation_id: str) -> None:
    conversation_uuid = uuid.UUID(conversation_id)
    conversation = _get_conversation_sync(conversation_uuid, open_only=True)
    if conversation is None:
        return

    if conversation.handover_status != "requested":
        return
    if not _timeout_handover_status_sync(conversation_uuid):
        return

    published, _ = _publish_to_channel_sync(
        "chat_runtime_events",
        json.dumps(
            {
                "type": "handover_timeout",
                "conversation_id": conversation_id,
            }
        ),
    )
    if not published:
        raise RuntimeError("Failed to publish handover timeout event")
