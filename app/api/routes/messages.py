import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import create_async_dashboard_db_session, get_async_chat_db
from app.models.chat_db_models import Messages
from app.models.dashboard_db_models import ConversationsMeta

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/conversations/{conversation_id}/latency")
async def get_conversation_latency(
    conversation_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_async_chat_db)],
):
    """Staff-only aggregate; NULL averages mean no measured successful replies."""
    claims = request.state.claims
    if claims.get("type") != "support_agent" or claims.get("conversation_id") != str(
        conversation_id
    ):
        raise HTTPException(status_code=403, detail="Conversation access denied")

    async with create_async_dashboard_db_session() as dashboard:
        exists = await dashboard.scalar(
            select(ConversationsMeta.id).where(
                ConversationsMeta.id == conversation_id,
                ConversationsMeta.organization_id == request.state.organization_id,
            )
        )
    if exists is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    row = (
        (
            await db.execute(
                text("""
        SELECT attempt_count, reply_count, average_response_ms, p50_response_ms,
            p95_response_ms, max_response_ms, failure_count, fallback_count,
            cancelled_count, cached_count, rejected_count
        FROM conversation_latency_stats
        WHERE organization_id = :org AND conversation_id = :conversation
    """),
                {"org": request.state.organization_id, "conversation": conversation_id},
            )
        )
        .mappings()
        .first()
    )
    values = (
        dict(row)
        if row
        else {
            "attempt_count": 0,
            "reply_count": 0,
            "average_response_ms": None,
            "p50_response_ms": None,
            "p95_response_ms": None,
            "max_response_ms": None,
            "failure_count": 0,
            "fallback_count": 0,
            "cancelled_count": 0,
            "cached_count": 0,
            "rejected_count": 0,
        }
    )
    return {
        "conversation_id": str(conversation_id),
        "measurement": "server_receive_to_response_send",
        "latency": values,
    }


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_async_chat_db)],
    request: Request,
):
    """Return history only for the caller's signed conversation scope."""

    try:
        claims = request.state.claims
        # A valid bot token must not grant access to another visitor's history.
        if claims.get("type") not in ("user", "support_agent") or claims.get(
            "conversation_id"
        ) != str(conversation_id):
            raise HTTPException(status_code=403, detail="Conversation access denied")

        conversation_uuid = conversation_id
        organization_id = request.state.organization_id
        try:
            bot_id = uuid.UUID(str(claims.get("bot_id")))
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(
                status_code=403, detail="Conversation access denied"
            ) from None

        async with create_async_dashboard_db_session() as dashboard:
            owned = await dashboard.scalar(
                select(ConversationsMeta.id).where(
                    ConversationsMeta.id == conversation_uuid,
                    ConversationsMeta.organization_id == organization_id,
                    ConversationsMeta.bot_id == bot_id,
                )
            )
        if owned is None:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages_result = await db.scalars(
            select(Messages)
            .where(Messages.conversation_id == conversation_uuid)
            .order_by(Messages.created_at.asc())
            .limit(50)
        )
        messages = [
            {
                "id": str(message.id),
                "conversation_id": str(message.conversation_id),
                "created_at": message.created_at.isoformat()
                if message.created_at
                else None,
                "agent_id": str(message.agent_id) if message.agent_id else None,
                "content_type": message.content_type,
                "content": message.content,
                "role": message.role,
            }
            for message in messages_result.all()
        ]
        return JSONResponse(content={"messages": messages}, status_code=200)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error getting messages")
        raise HTTPException(
            status_code=500,
            detail="Internal server error",
        ) from None
