from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.db.session import (
    create_async_chat_db_session,
    create_async_dashboard_db_session,
)
from app.models.chat_db_models import Messages
from app.models.dashboard_db_models import ConversationsMeta, OrganizationMembers

router = APIRouter()


@router.get("/billing/export")
async def export_chat(request: Request):
    claims = request.state.claims
    org_id = claims.get("organization_id")
    user_id = claims.get("user_id")
    if claims.get("role") != "billing_export" or not org_id or not user_id:
        raise HTTPException(403, "Organization export authorization required")
    async with create_async_dashboard_db_session() as db:
        member = await db.scalar(
            select(OrganizationMembers.id).where(
                OrganizationMembers.organization_id == org_id,
                OrganizationMembers.user_id == user_id,
                OrganizationMembers.role == "admin",
            )
        )
        if not member:
            raise HTTPException(403, "Organization administrator required")
        ids = list(
            (
                await db.scalars(
                    select(ConversationsMeta.id).where(
                        ConversationsMeta.organization_id == org_id
                    )
                )
            ).all()
        )
    async with create_async_chat_db_session() as db:
        # Conversation IDs are resolved from the authorized org, never accepted
        # from the caller. Stream-sized batches avoid a giant SQL IN expression.
        result = []
        for offset in range(0, len(ids), 500):
            rows = (
                await db.scalars(
                    select(Messages)
                    .where(Messages.conversation_id.in_(ids[offset : offset + 500]))
                    .order_by(Messages.created_at)
                )
            ).all()
            result.extend(
                {
                    "id": str(row.id),
                    "conversationId": str(row.conversation_id),
                    "role": row.role,
                    "content": row.content,
                    "contentType": row.content_type,
                    "createdAt": row.created_at.isoformat(),
                }
                for row in rows
            )
    return {"messages": result}
