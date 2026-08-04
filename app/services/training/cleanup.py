from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db.session import DashboardDbSessionLocal, SessionLocal
from app.infra.r2_storage import r2_delete_object, r2_object_exists
from app.models.chat_db_models import Documents, Embeddings, TrainingJobs
from app.models.dashboard_db_models import TrainingSources

logger = logging.getLogger(__name__)
_BUCKET = "bot-files"


def delete_training_source_job(
    job_id: str,
    source_id: str,
    organization_id: str,
    bot_id: str,
) -> None:
    """RQ cleanup task for a source that was already marked deleted."""
    if SessionLocal is None or DashboardDbSessionLocal is None:
        logger.critical("Database sessions not configured")
        return

    chat_session = SessionLocal()
    dashboard_session = DashboardDbSessionLocal()
    job: TrainingJobs | None = None

    try:
        job_uuid = uuid.UUID(job_id)
        source_uuid = uuid.UUID(source_id)
        bot_uuid = uuid.UUID(bot_id)

        job = chat_session.scalars(
            select(TrainingJobs).where(
                TrainingJobs.id == job_uuid,
                TrainingJobs.organization_id == organization_id,
                TrainingJobs.bot_id == bot_uuid,
            )
        ).one_or_none()
        if job is None:
            logger.error("Cleanup job not found", extra={"job_id": job_id})
            return

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        chat_session.commit()

        source = dashboard_session.scalars(
            select(TrainingSources).where(TrainingSources.id == source_uuid)
        ).one_or_none()
        if source is None:
            job.status = "cleanup_completed"
            job.completed_at = datetime.now(timezone.utc)
            chat_session.commit()
            return

        if source.deleted_at is None:
            raise ValueError("Source not marked as deleted")

        deleted_at = datetime.now(timezone.utc)
        chat_session.execute(
            update(Embeddings)
            .where(
                Embeddings.document_id.in_(
                    select(Documents.id).where(Documents.source_id == source_uuid)
                ),
                Embeddings.deleted_at.is_(None),
            )
            .values(deleted_at=deleted_at)
        )
        chat_session.execute(
            update(Documents)
            .where(
                Documents.source_id == source_uuid,
                Documents.deleted_at.is_(None),
            )
            .values(deleted_at=deleted_at)
        )
        chat_session.commit()

        if source.type == "file" and source.source_value:
            try:
                if r2_object_exists(_BUCKET, source.source_value):
                    r2_delete_object(_BUCKET, source.source_value)
                    logger.info(
                        "File deleted from R2",
                        extra={"source_id": source_id, "path": source.source_value},
                    )
            except Exception:
                logger.exception(
                    "Failed to delete file from R2",
                    extra={"source_id": source_id, "path": source.source_value},
                )

        job.status = "cleanup_completed"
        job.completed_at = datetime.now(timezone.utc)
        chat_session.commit()
        logger.info("Cleanup completed", extra={"job_id": job_id})
    except ValueError:
        if job is not None:
            try:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                chat_session.commit()
            except Exception:
                chat_session.rollback()
        raise
    except Exception as error:
        logger.exception(
            "Cleanup encountered errors; marking completed",
            extra={"job_id": job_id, "error": str(error)},
        )
        if job is not None:
            try:
                job.status = "cleanup_completed"
                job.completed_at = datetime.now(timezone.utc)
                chat_session.commit()
            except Exception:
                chat_session.rollback()
    finally:
        dashboard_session.close()
        chat_session.close()
