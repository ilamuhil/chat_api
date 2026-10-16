from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from app.services.conversation_lifecycle import (
    close_conversation,
    publish_conversation_closed,
)

router = APIRouter()


@router.post("/conversations/{conversation_id}/close")
async def close_from_dashboard(conversation_id: UUID, request: Request):
    claims = request.state.claims
    if claims.get("type") != "support_agent" or claims.get("conversation_id") != str(
        conversation_id
    ):
        raise HTTPException(status_code=403, detail="Conversation access denied")
    if not await close_conversation(
        conversation_id,
        closed_by="support_agent",
        organization_id=request.state.organization_id,
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")
    await publish_conversation_closed(conversation_id, "support_agent")
    return {"id": str(conversation_id), "status": "closed"}
