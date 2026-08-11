import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_chat_db
from app.models.chat_db_models import Messages

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get('/conversations/{conversation_id}/messages')
async def get_messages(conversation_id: str,   db: Session = Depends(get_chat_db)):
  """Given the conversation id return all the messages in the conversation from the messages table"""
  
  try:
    #! organization level authorization check is done by the dashboard server. No need to check again here. 
    
    if not conversation_id:
      logger.error("Conversation id not found in claims")
      raise HTTPException(
        status_code=401,
        detail="Conversation not found in claims",
      )
    conversation_uuid = uuid.UUID(conversation_id)
   
    results = db.execute(
        select(
            Messages.id,
            Messages.conversation_id,
            Messages.created_at,
            Messages.agent_id,
            Messages.content_type,
            Messages.content,
            Messages.role
        ).where(Messages.conversation_id == conversation_uuid).order_by(Messages.created_at.asc()).limit(50)
    ).all()
    
    messages = [
        {
            "id": str(row.id),
            "conversation_id": str(row.conversation_id),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "agent_id": str(row.agent_id) if row.agent_id else None,
            "content_type": row.content_type,
            "content": row.content,
            "role": row.role
        }
        for row in results
    ]
    return JSONResponse(content={"messages": messages}, status_code=200)
  except HTTPException as e:
    raise e  
  except Exception as e:
    logger.exception("Unexpected error getting messages")
    raise HTTPException(
        status_code=500,
        detail="Internal server error",
    )
  
  
  
  