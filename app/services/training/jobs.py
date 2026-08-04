from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.session import DashboardDbSessionLocal, SessionLocal
from app.models.chat_db_models import (
    BotConfigurations,
    EmbeddingConfigurations,
    TrainingJobs,
)
from app.models.dashboard_db_models import TrainingSources
from app.services.training.ingestion import (
    process_file_training_source,
    process_url_training_source,
)

logger = logging.getLogger(__name__)


def _mark_source_failed(
    dashboard_session: Session, source: TrainingSources, error: Exception
) -> None:
    """Persist a source failure without leaving its session in a failed state."""
    source_id = source.id
    dashboard_session.rollback()
    try:
        source = dashboard_session.scalars(
            select(TrainingSources).where(TrainingSources.id == source_id)
        ).one()
        source.status = "training_failed"
        source.error_message = str(error)
        dashboard_session.commit()
    except Exception:
        dashboard_session.rollback()
        logger.exception(
            "Failed to update training source after processing error",
            extra={"source_id": str(source_id)},
        )


def _get_job_config(
    chat_session: Session, job: TrainingJobs, bot_id: uuid.UUID
) -> EmbeddingConfigurations:
    config = chat_session.scalars(
        select(EmbeddingConfigurations).where(
            EmbeddingConfigurations.id == job.embedding_configuration_id,
            EmbeddingConfigurations.bot_id == bot_id,
        )
    ).one_or_none()
    if config is None:
        raise ValueError(
            "Training job references an unavailable embedding configuration"
        )
    bot_config = chat_session.scalars(
        select(BotConfigurations).where(
            BotConfigurations.id == job.bot_configuration_id,
            BotConfigurations.bot_id == bot_id,
            BotConfigurations.embedding_configuration_id == config.id,
        )
    ).one_or_none()
    if bot_config is None:
        raise ValueError(
            "Training job references an unavailable bot configuration"
        )
    return config


def _activate_configurations(
    chat_session: Session,
    embedding_configuration_id: uuid.UUID,
    bot_configuration_id: uuid.UUID,
    bot_id: uuid.UUID,
) -> None:
    chat_session.execute(
        update(EmbeddingConfigurations)
        .where(
            EmbeddingConfigurations.bot_id == bot_id,
            EmbeddingConfigurations.id != embedding_configuration_id,
            EmbeddingConfigurations.state == "active",
        )
        .values(state="deprecated")
    )
    chat_session.execute(
        update(BotConfigurations)
        .where(
            BotConfigurations.bot_id == bot_id,
            BotConfigurations.id != bot_configuration_id,
            BotConfigurations.state == "active",
        )
        .values(state="deprecated")
    )
    chat_session.execute(
        update(EmbeddingConfigurations)
        .where(EmbeddingConfigurations.id == embedding_configuration_id)
        .values(state="active")
    )
    chat_session.execute(
        update(BotConfigurations)
        .where(BotConfigurations.id == bot_configuration_id)
        .values(state="active")
    )


def process_training_job(
    job_id: str,
    bot_id: str,
    organization_id: str,
    source_ids: Sequence[str],
) -> None:
    """RQ entry point for processing all selected sources in one training job."""
    if SessionLocal is None or DashboardDbSessionLocal is None:
        logger.critical("Database sessions not configured")
        return

    chat_session = SessionLocal()
    dashboard_session = DashboardDbSessionLocal()
    job: TrainingJobs | None = None

    try:
        job_uuid = uuid.UUID(job_id)
        bot_uuid = uuid.UUID(bot_id)
        job = chat_session.scalars(
            select(TrainingJobs).where(TrainingJobs.id == job_uuid)
        ).one()
        config = _get_job_config(chat_session, job, bot_uuid)

        if config.state != "active":
            config.state = "training"
            bot_config = chat_session.scalars(
                select(BotConfigurations).where(
                    BotConfigurations.id == job.bot_configuration_id
                )
            ).one()
            bot_config.state = "training"
            chat_session.commit()

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        chat_session.commit()

        source_uuids = [uuid.UUID(source_id) for source_id in source_ids]
        sources = dashboard_session.scalars(
            select(TrainingSources).where(
                TrainingSources.id.in_(source_uuids),
                TrainingSources.bot_id == bot_uuid,
                TrainingSources.organization_id == organization_id,
            )
        ).all()

        any_successful = False
        any_failed = False
        for source in sources:
            try:
                source.status = "training"
                dashboard_session.commit()
                logger.info(
                    "Processing training source",
                    extra={"job_id": job_id, "source_id": str(source.id)},
                )

                if source.type == "url":
                    process_url_training_source(source, chat_session, config)
                else:
                    process_file_training_source(
                        source, chat_session, dashboard_session, config
                    )

                source.status = "trained"
                dashboard_session.commit()
                any_successful = True
            except Exception as error:
                any_failed = True
                chat_session.rollback()
                logger.exception(
                    "Failed to process training source",
                    extra={
                        "job_id": job_id,
                        "source_id": str(source.id),
                        "error": str(error),
                    },
                )
                _mark_source_failed(dashboard_session, source, error)

        if any_successful and not any_failed:
            _activate_configurations(
                chat_session,
                config.id,
                job.bot_configuration_id,
                bot_uuid,
            )
        elif config.state == "training":
            config.state = "failed"
            chat_session.execute(
                update(BotConfigurations)
                .where(BotConfigurations.id == job.bot_configuration_id)
                .values(state="failed")
            )

        job.status = (
            "completed"
            if any_successful and not any_failed
            else "partially_completed"
            if any_successful
            else "failed"
        )
        job.completed_at = datetime.now(timezone.utc)
        chat_session.commit()
        logger.info(
            "Training job finished",
            extra={"job_id": job_id, "status": job.status},
        )
    except Exception as error:
        logger.exception(
            "Training job crashed",
            extra={"job_id": job_id, "error": str(error)},
        )
        chat_session.rollback()
        dashboard_session.rollback()
        if job is not None:
            for model in (
                EmbeddingConfigurations,
                BotConfigurations,
            ):
                chat_session.execute(
                    update(model)
                    .where(model.id.in_(
                        [job.embedding_configuration_id, job.bot_configuration_id]
                    ))
                    .values(state="failed")
                )
        if job is not None:
            try:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                chat_session.commit()
            except Exception:
                chat_session.rollback()
    finally:
        dashboard_session.close()
        chat_session.close()
