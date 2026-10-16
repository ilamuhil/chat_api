# This function is run by a scheduler to close all stale conversations. Runs every 15 min and conversation idle timeout period is 15 min.

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, update

from app.config.logging_config import setup_logging
from app.core.env import load_app_env
from app.db.session import create_async_dashboard_db_session
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
    try:
        async with create_async_dashboard_db_session() as db:
            # update all conversations with the status open and last_message_at more than 30 min and handover_status not in requested
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
                    closed_at=datetime.now(UTC),
                    summary_status="pending",
                )
            )
            closed_ids = list(
                (await db.scalars(update_stmnt.returning(ConversationsMeta.id))).all()
            )
            await db.commit()
            logger.info("Stale conversations closed by system")
        for conversation_id in closed_ids:
            await enqueue_conversation_summary(conversation_id)
            await publish_conversation_closed(conversation_id, "system")
    except Exception as e:
        logger.exception(
            "Error occured while closing stale conversations", extra={"error": str(e)}
        )

    await queue_pending_summaries()
