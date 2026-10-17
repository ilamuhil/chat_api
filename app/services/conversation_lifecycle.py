"""One closure boundary for socket, dashboard, and timeout paths."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from redis import Redis
from rq import Queue, Retry
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import Job
from sqlalchemy import select

from app.db.session import create_async_dashboard_db_session
from app.infra.db_retry import retry_db_operation
from app.infra.redis_client import REDIS_URL
from app.models.dashboard_db_models import ConversationsMeta
from app.services.lead_summary import summarize_conversation
from app.ws.agent_tool_api import RUNTIME_EVENTS_CHANNEL, publish_to_channel

logger = logging.getLogger(__name__)

# RQ stores compressed binary job data; the JSON cache client decodes UTF-8.
redis_client = Redis.from_url(REDIS_URL, decode_responses=False)


def _enqueue_summary(conversation_id: UUID) -> None:
    job_id = f"conversation-summary-{conversation_id}"
    try:
        existing = Job.fetch(job_id, connection=redis_client)
    except NoSuchJobError:
        existing = None
    if existing:
        status = existing.get_status()
        if status in {"queued", "started", "scheduled", "deferred"}:
            return
        if status == "failed":
            existing.requeue()
            return
        existing.delete()
    try:
        Queue("default", connection=redis_client).enqueue(
            summarize_conversation,
            conversation_id,
            job_id=job_id,
            unique=True,
            result_ttl=0,
            job_timeout=180,
            retry=Retry(max=3, interval=[10, 60, 300]),
        )
    except DuplicateJobError:
        pass


async def enqueue_conversation_summary(conversation_id: UUID) -> bool:
    try:
        await asyncio.to_thread(_enqueue_summary, conversation_id)
        return True
    except Exception:
        # Durable pending status lets the existing scheduled job recover this.
        logger.exception(
            "Summary enqueue failed; scheduled recovery will retry",
            extra={"conversation_id": str(conversation_id)},
        )
        return False


async def close_conversation(
    conversation_id: UUID, *, closed_by: str, organization_id: str | None = None
) -> bool:
    async with create_async_dashboard_db_session() as db:
        query = select(ConversationsMeta).where(ConversationsMeta.id == conversation_id)
        if organization_id is not None:
            query = query.where(ConversationsMeta.organization_id == organization_id)
        conversation = await db.scalar(query.with_for_update())
        if conversation is None:
            return False
        if conversation.status != "closed":
            conversation.status = "closed"
            conversation.closed_by = closed_by
            conversation.closed_at = datetime.now(UTC)
            conversation.summary_status = "pending"
        needs_summary = conversation.summary_status in {"pending", "failed"}
        await db.commit()
    if needs_summary:
        await enqueue_conversation_summary(conversation_id)
    return True


async def publish_conversation_closed(conversation_id: UUID, closed_by: str) -> None:
    await publish_to_channel(
        RUNTIME_EVENTS_CHANNEL,
        {
            "type": "conversation_closed",
            "conversation_id": str(conversation_id),
            "closed_by": closed_by,
        },
    )


async def queue_pending_summaries() -> None:
    async def read_pending() -> list[UUID]:
        async with create_async_dashboard_db_session() as db:
            return list(
                (
                    await db.scalars(
                        select(ConversationsMeta.id)
                        .where(
                            ConversationsMeta.status == "closed",
                            ConversationsMeta.summary_status.in_(["pending", "failed"]),
                        )
                        .order_by(ConversationsMeta.closed_at)
                        .limit(50)
                    )
                ).all()
            )

    ids = await retry_db_operation(read_pending, name="recover pending summaries")
    for conversation_id in ids:
        await enqueue_conversation_summary(conversation_id)
