import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_chat_db
from app.models.chat_db_models import Messages

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(
    conversation_id: str,
    db: Annotated[AsyncSession, Depends(get_async_chat_db)],
):
    """Given the conversation id return all the messages in the conversation from the messages table"""

    try:
        # ! organization level authorization check is done by the dashboard server. No need to check again here.

        if not conversation_id:
            logger.error("Conversation id not found in claims")
            raise HTTPException(
                status_code=401,
                detail="Conversation not found in claims",
            )
        conversation_uuid = uuid.UUID(conversation_id)

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
