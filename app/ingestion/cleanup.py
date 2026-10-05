from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.db.session import DashboardDbSessionLocal, SessionLocal
from app.infra.r2_storage import r2_delete_object, r2_object_exists
from app.ingestion.errors import TrainingFailure, append_errors, error_records
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
        raise RuntimeError("Training database sessions are not configured")

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
        job.started_at = datetime.now(UTC)
        chat_session.commit()

        source = dashboard_session.scalars(
            select(TrainingSources).where(
                TrainingSources.id == source_uuid,
                TrainingSources.bot_id == bot_uuid,
                TrainingSources.organization_id == organization_id,
            )
        ).one_or_none()
        if source is None:
            job.status = "cleanup_completed"
            job.completed_at = datetime.now(UTC)
            chat_session.commit()
            return

        if source.deleted_at is None:
            raise TrainingFailure(
                "source_not_deleted",
                "This source has not been marked for removal.",
                stage="cleanup",
                action="Remove the source from the dashboard before running cleanup.",
                retryable=False,
            )

        deleted_at = datetime.now(UTC)
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
                    "Failed to delete file from R2", extra={"source_id": source_id}
                )
                raise

        job.status = "cleanup_completed"
        job.completed_at = datetime.now(UTC)
        chat_session.commit()
        logger.info("Cleanup completed", extra={"job_id": job_id})
    except Exception as error:
        logger.exception(
            "Source cleanup failed", extra={"job_id": job_id, "source_id": source_id}
        )
        chat_session.rollback()
        dashboard_session.rollback()
        records = error_records(
            error, stage="cleanup", job_id=job_id, source_id=source_id
        )
        try:
            failed_source = dashboard_session.scalars(
                select(TrainingSources)
                .where(
                    TrainingSources.id == uuid.UUID(source_id),
                    TrainingSources.bot_id == uuid.UUID(bot_id),
                    TrainingSources.organization_id == organization_id,
                )
                .with_for_update()
            ).one_or_none()
            if failed_source is not None:
                failed_source.error_message = append_errors(
                    failed_source.error_message, records
                )
                dashboard_session.commit()
        except Exception as reporting_error:
            dashboard_session.rollback()
            logger.exception("Could not record source cleanup errors")
            records.extend(
                error_records(
                    reporting_error,
                    stage="source_status",
                    job_id=job_id,
                    source_id=source_id,
                )
            )
        if job is None:
            raise
        job.status = "failed"
        job.error_message = append_errors(job.error_message, records)
        job.completed_at = datetime.now(UTC)
        chat_session.commit()
    finally:
        dashboard_session.close()
        chat_session.close()
