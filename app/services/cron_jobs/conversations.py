# This function is run by a scheduler to close all stale conversations. Runs every 15 min and conversation idle timeout period is 15 min.

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.config.logging_config import setup_logging
from app.core.env import load_app_env
from app.db.session import DashboardDbSessionLocal
from app.models.dashboard_db_models import ConversationsMeta

load_app_env()
setup_logging()

logger = logging.getLogger(__name__)


async def close_stale_conversations() -> None:
    # conversations older than 15 min are closed by system
    cutoff_time = datetime.now(UTC) - timedelta(minutes=15)
    try:
        with DashboardDbSessionLocal() as db:
            # update all conversations with the status open and last_message_at more than 30 min and handover_status not in requested
            update_stmnt = (
                update(ConversationsMeta)
                .where(
                    ConversationsMeta.status == "open",
                    ConversationsMeta.handover_status != "requested",
                    ConversationsMeta.last_message_at < cutoff_time,
                )
                .values(
                    status="closed", closed_by="system", closed_at=datetime.now(UTC)
                )
            )
            db.execute(update_stmnt)
            db.commit()
            logger.info("Stale conversations closed by system")
    except Exception as e:
        logger.exception(
            "Error occured while closing stale conversations", extra={"error": str(e)}
        )
