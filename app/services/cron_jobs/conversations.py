# This function is run by a scheduler to close all stale conversations. Runs every 15 min and conversation idle timeout period is 15 min.

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update

from app.config.logging_config import setup_logging
from app.core.env import load_app_env
from app.db.session import create_async_dashboard_db_session
from app.infra.db_retry import retry_db_operation
from app.models.dashboard_db_models import ConversationsMeta
from app.services.conversation_lifecycle import (
    enqueue_conversation_summary,
    publish_conversation_closed,
    queue_pending_summaries,
)

load_app_env()
setup_logging()

logger = logging.getLogger(__name__)


async def close_stale_conversations() -> None:
    # conversations older than 15 min are closed by system
    cutoff_time = datetime.now(UTC) - timedelta(minutes=15)
    # Retain IDs across an uncertain commit so a successful retry also notifies
    # chats whose first commit succeeded but lost its acknowledgement.
    attempted_ids = set()
    closed_at = datetime.now(UTC)

    async def close_batch() -> set:
        async with create_async_dashboard_db_session() as db:
            closed_ids = set()
            if attempted_ids:
                # A rolled-back attempt may have raced with renewed activity.
                # Notify only rows actually closed by this invocation.
                closed_ids.update(
                    (
                        await db.scalars(
                            select(ConversationsMeta.id).where(
                                ConversationsMeta.id.in_(attempted_ids),
                                ConversationsMeta.status == "closed",
                                ConversationsMeta.closed_by == "system",
                                ConversationsMeta.closed_at == closed_at,
                            )
                        )
                    ).all()
                )
            # Close idle chats without interrupting a requested human handover.
            update_stmnt = (
                update(ConversationsMeta)
                .where(
                    ConversationsMeta.status == "open",
                    or_(
                        ConversationsMeta.handover_status != "requested",
                        ConversationsMeta.handover_status.is_(None),
                    ),
                    or_(
                        ConversationsMeta.last_message_at < cutoff_time,
                        (ConversationsMeta.last_message_at.is_(None))
                        & (ConversationsMeta.created_at < cutoff_time),
                    ),
                )
                .values(
                    status="closed",
                    closed_by="system",
                    closed_at=closed_at,
                    summary_status="pending",
                )
            )
            closed_ids.update(
                (await db.scalars(update_stmnt.returning(ConversationsMeta.id))).all()
            )
            attempted_ids.update(closed_ids)
            await db.commit()
            logger.info("Stale conversations closed by system")
            return closed_ids

    # Let the worker report an exhausted failure once. Do not immediately query
    # the same unavailable database again for summary recovery.
    closed_ids = await retry_db_operation(close_batch, name="close stale conversations")
    for conversation_id in closed_ids:
        await enqueue_conversation_summary(conversation_id)
        try:
            await publish_conversation_closed(conversation_id, "system")
        except Exception:
            logger.exception(
                "Could not publish stale conversation closure",
                extra={"conversation_id": str(conversation_id)},
            )
    await queue_pending_summaries()
