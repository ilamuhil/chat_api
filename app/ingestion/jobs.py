from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.session import DashboardDbSessionLocal, SessionLocal
from app.ingestion.errors import (
    TrainingError,
    TrainingFailure,
    append_errors,
    error_records,
    error_stage,
    resolve_errors,
)
from app.ingestion.pipeline import IngestionPipeline
from app.models.chat_db_models import (
    BotConfigurations,
    EmbeddingConfigurations,
    TrainingJobs,
)
from app.models.dashboard_db_models import TrainingSources

logger = logging.getLogger(__name__)


def _mark_source_failed(
    dashboard_session: Session,
    source_id: uuid.UUID,
    bot_id: uuid.UUID,
    organization_id: str,
    errors: list[TrainingError],
    *,
    unfinished_only: bool = False,
) -> bool:
    """Append failures under a row lock; return False for a removed source."""
    dashboard_session.rollback()
    source = dashboard_session.scalars(
        select(TrainingSources)
        .where(
            TrainingSources.id == source_id,
            TrainingSources.bot_id == bot_id,
            TrainingSources.organization_id == organization_id,
            TrainingSources.deleted_at.is_(None),
        )
        .with_for_update()
    ).one_or_none()
    if source is None or (unfinished_only and source.status == "trained"):
        return False
    source.status = "training_failed"
    source.error_message = append_errors(source.error_message, errors)
    dashboard_session.commit()
    return True


def _get_job_config(
    chat_session: Session, job: TrainingJobs, bot_id: uuid.UUID
) -> EmbeddingConfigurations:
    config = chat_session.scalars(
        select(EmbeddingConfigurations).where(
            EmbeddingConfigurations.id == job.embedding_configuration_id,
            EmbeddingConfigurations.bot_id == bot_id,
            EmbeddingConfigurations.state.in_(["draft", "training", "active"]),
        )
    ).one_or_none()
    if config is None:
        raise TrainingFailure(
            "embedding_configuration_unavailable",
            "The bot's embedding settings are unavailable.",
            stage="configuration",
            action="Ask an administrator to configure the bot, then retry training.",
            retryable=False,
        )
    bot_config = chat_session.scalars(
        select(BotConfigurations).where(
            BotConfigurations.id == job.bot_configuration_id,
            BotConfigurations.bot_id == bot_id,
            BotConfigurations.embedding_configuration_id == config.id,
            BotConfigurations.state.in_(["draft", "training", "active"]),
        )
    ).one_or_none()
    if bot_config is None:
        raise TrainingFailure(
            "bot_configuration_unavailable",
            "The bot's model settings are unavailable.",
            stage="configuration",
            action="Ask an administrator to configure the bot, then retry training.",
            retryable=False,
        )
    return config


def _reset_training_configurations(chat_session: Session, job: TrainingJobs) -> None:
    for model, configuration_id in (
        (EmbeddingConfigurations, job.embedding_configuration_id),
        (BotConfigurations, job.bot_configuration_id),
    ):
        chat_session.execute(
            update(model)
            .where(
                model.id == configuration_id,
                model.bot_id == job.bot_id,
                model.state == "training",
            )
            .values(state="draft")
        )


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


def _persist_job_failure(
    chat_session: Session,
    dashboard_session: Session,
    job: TrainingJobs,
    source_ids: Sequence[uuid.UUID],
    error: Exception,
    *,
    stage: str,
    unfinished_only: bool = True,
) -> None:
    """Record a run-level error on the job and every unfinished selected source."""
    job_id, bot_id, organization_id = str(job.id), job.bot_id, job.organization_id
    failures = error_records(error, stage=stage, job_id=job_id)
    for source_id in source_ids:
        source_errors = [{**record, "source_id": str(source_id)} for record in failures]
        try:
            _mark_source_failed(
                dashboard_session,
                source_id,
                bot_id,
                organization_id,
                source_errors,
                unfinished_only=unfinished_only,
            )
        except Exception as reporting_error:
            dashboard_session.rollback()
            logger.exception(
                "Could not record source failure", extra={"source_id": str(source_id)}
            )
            failures.extend(
                error_records(
                    reporting_error,
                    stage="source_status",
                    job_id=job_id,
                    source_id=str(source_id),
                )
            )
    _reset_training_configurations(chat_session, job)
    job.status = "failed"
    job.source_ids = [str(source_id) for source_id in source_ids]
    job.error_message = append_errors(job.error_message, failures)
    job.completed_at = datetime.now(UTC)
    chat_session.commit()


def process_training_job(
    job_id: str, bot_id: str, organization_id: str, source_ids: Sequence[str]
) -> None:
    """Train selected sources; failures belong to sources and the job."""
    if SessionLocal is None or DashboardDbSessionLocal is None:
        raise RuntimeError("Training database sessions are not configured")
    chat_session = SessionLocal()
    dashboard_session = DashboardDbSessionLocal()
    job: TrainingJobs | None = None
    requested: list[uuid.UUID] = []
    finalizing = False
    try:
        job_uuid, bot_uuid = uuid.UUID(job_id), uuid.UUID(bot_id)
        requested = list(
            dict.fromkeys(uuid.UUID(source_id) for source_id in source_ids)
        )
        job = chat_session.scalars(
            select(TrainingJobs).where(
                TrainingJobs.id == job_uuid,
                TrainingJobs.bot_id == bot_uuid,
                TrainingJobs.organization_id == organization_id,
            )
        ).one()
        job.source_ids = [str(source_id) for source_id in requested]
        with error_stage("configuration"):
            config = _get_job_config(chat_session, job, bot_uuid)
        if config.state != "active":
            config.state = "training"
            chat_session.execute(
                update(BotConfigurations)
                .where(
                    BotConfigurations.id == job.bot_configuration_id,
                    BotConfigurations.state == "draft",
                )
                .values(state="training")
            )
        job.status = "processing"
        job.started_at = datetime.now(UTC)
        chat_session.commit()

        sources = dashboard_session.scalars(
            select(TrainingSources).where(
                TrainingSources.id.in_(requested),
                TrainingSources.bot_id == bot_uuid,
                TrainingSources.organization_id == organization_id,
                TrainingSources.deleted_at.is_(None),
            )
        ).all()
        found = {source.id for source in sources}
        failures: list[TrainingError] = []
        for missing_id in set(requested) - found:
            failures.extend(
                error_records(
                    TrainingFailure(
                        "source_unavailable",
                        "A selected source was deleted or is no longer available.",
                        action="Refresh the training sources and retry the available sources.",
                        retryable=False,
                    ),
                    stage="validation",
                    job_id=job_id,
                    source_id=str(missing_id),
                )
            )
        successful = 0
        for source in sources:
            source_id = source.id
            try:
                with error_stage("source_status"):
                    source.status = "training"
                    dashboard_session.commit()
                logger.info(
                    "Processing training source",
                    extra={"job_id": job_id, "source_id": str(source_id)},
                )
                IngestionPipeline(source, chat_session, dashboard_session, config).run()
                with error_stage("source_status"):
                    source.status = "trained"
                    source.last_trained_at = datetime.now(UTC)
                    source.error_message = resolve_errors(source.error_message)
                    dashboard_session.commit()
                successful += 1
            except Exception as error:
                chat_session.rollback()
                logger.exception(
                    "Failed to process training source",
                    extra={"job_id": job_id, "source_id": str(source_id)},
                )
                records = error_records(
                    error, stage="worker", job_id=job_id, source_id=str(source_id)
                )
                failures.extend(records)
                try:
                    _mark_source_failed(
                        dashboard_session, source_id, bot_uuid, organization_id, records
                    )
                except Exception as reporting_error:
                    dashboard_session.rollback()
                    logger.exception(
                        "Failed to save source errors",
                        extra={"source_id": str(source_id)},
                    )
                    failures.extend(
                        error_records(
                            reporting_error,
                            stage="source_status",
                            job_id=job_id,
                            source_id=str(source_id),
                        )
                    )

        finalizing = True
        if successful and not failures:
            _activate_configurations(
                chat_session, config.id, job.bot_configuration_id, bot_uuid
            )
        else:
            _reset_training_configurations(chat_session, job)
        job.status = (
            "completed"
            if successful and not failures
            else "partially_completed"
            if successful
            else "failed"
        )
        job.error_message = append_errors(job.error_message, failures)
        job.completed_at = datetime.now(UTC)
        chat_session.commit()
        logger.info(
            "Training job finished", extra={"job_id": job_id, "status": job.status}
        )
    except Exception as error:
        logger.exception("Training job crashed", extra={"job_id": job_id})
        chat_session.rollback()
        dashboard_session.rollback()
        if job is None:
            raise
        try:
            _persist_job_failure(
                chat_session,
                dashboard_session,
                job,
                requested,
                error,
                stage="job",
                unfinished_only=not finalizing,
            )
        except Exception as reporting_error:
            chat_session.rollback()
            raise ExceptionGroup(
                "Training failed and its result could not be saved",
                [error, reporting_error],
            ) from error
    finally:
        dashboard_session.close()
        chat_session.close()


def training_job_failure_callback(
    rq_job, _connection, _type, error, _traceback
) -> None:
    """Handle RQ timeouts or worker failures that escape normal processing."""
    job_id, bot_id, organization_id, source_ids = rq_job.args
    records = error_records(error, stage="worker", job_id=job_id)
    # Redis retains a fallback when a database outage prevents DB error reporting.
    rq_job.meta["training_errors"] = records
    try:
        rq_job.save_meta()
    except Exception:
        logger.exception(
            "Could not store worker error metadata", extra={"job_id": job_id}
        )
        error = ExceptionGroup(
            "Training and fallback error reporting failed",
            [
                error,
                TrainingFailure(
                    "worker_error_reporting_failed",
                    "The worker could not save its fallback error record.",
                    stage="worker",
                    action="Retry the failed sources after the training service is available.",
                ),
            ],
        )
    if SessionLocal is None or DashboardDbSessionLocal is None:
        logger.error(
            "Cannot record worker failure: database sessions unavailable",
            extra={"job_id": job_id},
        )
        return
    with SessionLocal() as chat_session, DashboardDbSessionLocal() as dashboard_session:
        job = chat_session.scalars(
            select(TrainingJobs).where(
                TrainingJobs.id == uuid.UUID(job_id),
                TrainingJobs.bot_id == uuid.UUID(bot_id),
                TrainingJobs.organization_id == organization_id,
            )
        ).one_or_none()
        if job is not None and job.status in {"queued", "processing"}:
            _persist_job_failure(
                chat_session,
                dashboard_session,
                job,
                [uuid.UUID(source_id) for source_id in source_ids],
                error,
                stage="worker",
            )
