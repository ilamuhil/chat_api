from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from rq import Callback, Queue
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_async_chat_db, get_async_dashboard_db
from app.infra.redis_client import redis_client
from app.ingestion.cleanup import delete_training_source_job
from app.ingestion.errors import TrainingFailure, append_errors, error_records
from app.ingestion.jobs import process_training_job, training_job_failure_callback
from app.models.chat_db_models import (
    BotConfigurations,
    EmbeddingConfigurations,
    TrainingJobs,
)
from app.models.dashboard_db_models import TrainingSources

logger = logging.getLogger(__name__)

router = APIRouter()


async def _record_queue_failure(
    chat_db: AsyncSession,
    dashboard_db: AsyncSession,
    *,
    error: Exception,
    bot_id: uuid.UUID,
    organization_id: str,
    job_id: uuid.UUID | None,
    source_ids: list[uuid.UUID] | None,
):
    records = error_records(
        error, stage="queue", job_id=str(job_id) if job_id else None
    )
    try:
        await dashboard_db.rollback()
        statement = select(TrainingSources).where(
            TrainingSources.bot_id == bot_id,
            TrainingSources.organization_id == organization_id,
            TrainingSources.deleted_at.is_(None),
        )
        if source_ids is None:
            statement = statement.where(
                TrainingSources.status.in_(["created", "training_failed"])
            )
        else:
            statement = statement.where(TrainingSources.id.in_(source_ids))
        sources = (await dashboard_db.scalars(statement.with_for_update())).all()
        for source in sources:
            source.status = "training_failed"
            source.error_message = append_errors(
                source.error_message,
                [{**record, "source_id": str(source.id)} for record in records],
            )
        await dashboard_db.commit()
    except Exception as reporting_error:
        await dashboard_db.rollback()
        logger.exception("Could not save source queue errors")
        records.extend(
            error_records(
                reporting_error,
                stage="source_status",
                job_id=str(job_id) if job_id else None,
            )
        )
    if job_id is not None:
        try:
            await chat_db.rollback()
            job = (
                await chat_db.scalars(
                    select(TrainingJobs)
                    .where(
                        TrainingJobs.id == job_id,
                        TrainingJobs.bot_id == bot_id,
                        TrainingJobs.organization_id == organization_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if job is not None:
                job.status = "failed"
                job.error_message = append_errors(job.error_message, records)
                job.completed_at = datetime.now(UTC)
                await chat_db.commit()
        except Exception as reporting_error:
            await chat_db.rollback()
            logger.exception("Could not save training job queue errors")
            records.extend(
                error_records(reporting_error, stage="job", job_id=str(job_id))
            )
    return records


@router.post("/training/queue")
async def queue_training(
    request: Request,
    dashboard_db: Annotated[AsyncSession, Depends(get_async_dashboard_db)],
    chat_db: Annotated[AsyncSession, Depends(get_async_chat_db)],
):
    organization_id = request.state.claims.get("organization_id")
    try:
        data = await request.json()
        bot_uuid = uuid.UUID(str(data.get("bot_id")))
        retry_failed = data.get("retry_failed", False)
        if not isinstance(retry_failed, bool) or not organization_id:
            raise ValueError("Invalid request")
        requested_ids = data.get("source_ids")
        if requested_ids is not None:
            if not isinstance(requested_ids, list) or not requested_ids:
                raise ValueError("Select at least one source")
            requested_ids = list(
                dict.fromkeys(uuid.UUID(str(source_id)) for source_id in requested_ids)
            )
    except (ValueError, TypeError, AttributeError):
        errors = error_records(
            TrainingFailure(
                "invalid_training_request",
                "Select a valid bot and at least one valid source.",
                action="Refresh the training page and select the sources again.",
                retryable=False,
            ),
            stage="validation",
        )
        return JSONResponse(
            {"error": errors[0]["message"], "errors": errors}, status_code=400
        )

    job_uuid: uuid.UUID | None = None
    selected_ids: list[uuid.UUID] | None = None
    try:
        existing = (
            await chat_db.scalars(
                select(TrainingJobs).where(
                    TrainingJobs.bot_id == bot_uuid,
                    TrainingJobs.organization_id == organization_id,
                    TrainingJobs.status.in_(["queued", "processing"]),
                )
            )
        ).first()
        if existing:
            return JSONResponse(
                {"error": "Training already in progress for this bot"}, status_code=409
            )
        statement = select(TrainingSources).where(
            TrainingSources.bot_id == bot_uuid,
            TrainingSources.organization_id == organization_id,
            TrainingSources.status.in_(
                ["training_failed"] if retry_failed else ["created", "training_failed"]
            ),
            TrainingSources.deleted_at.is_(None),
        )
        if requested_ids is not None:
            statement = statement.where(TrainingSources.id.in_(requested_ids))
        sources = (await dashboard_db.scalars(statement)).all()
        selected_ids = [source.id for source in sources]
        if requested_ids is not None and set(selected_ids) != set(requested_ids):
            return JSONResponse(
                {
                    "error": "Some selected sources are unavailable or are not eligible for training."
                },
                status_code=400,
            )
        if not sources:
            return JSONResponse(
                {"message": "No eligible sources to train", "source_ids": []},
                status_code=200,
            )
        config = (
            await chat_db.scalars(
                select(EmbeddingConfigurations)
                .where(
                    EmbeddingConfigurations.bot_id == bot_uuid,
                    EmbeddingConfigurations.state == "active",
                )
                .order_by(desc(EmbeddingConfigurations.created_at))
            )
        ).first()
        if config is None:
            config = (
                await chat_db.scalars(
                    select(EmbeddingConfigurations)
                    .where(
                        EmbeddingConfigurations.bot_id == bot_uuid,
                        EmbeddingConfigurations.state == "draft",
                    )
                    .order_by(desc(EmbeddingConfigurations.created_at))
                )
            ).first()
        bot_config = None
        if config is not None:
            bot_config = (
                await chat_db.scalars(
                    select(BotConfigurations)
                    .where(
                        BotConfigurations.bot_id == bot_uuid,
                        BotConfigurations.embedding_configuration_id == config.id,
                        BotConfigurations.state.in_(["active", "draft"]),
                    )
                    .order_by(desc(BotConfigurations.created_at))
                )
            ).first()
        if config is None or bot_config is None:
            raise TrainingFailure(
                "model_configuration_missing",
                "This bot has no usable model settings.",
                stage="configuration",
                action="Ask an administrator to configure the bot, then retry the failed sources.",
                retryable=False,
            )
        job_uuid = uuid.uuid4()
        chat_db.add(
            TrainingJobs(
                id=job_uuid,
                organization_id=organization_id,
                bot_id=bot_uuid,
                status="queued",
                embedding_configuration_id=config.id,
                bot_configuration_id=bot_config.id,
                source_ids=[str(source_id) for source_id in selected_ids],
                error_message=[],
            )
        )
        await chat_db.commit()
        for source in sources:
            source.status = "queued_for_training"
        await dashboard_db.commit()
        Queue(connection=redis_client).enqueue(
            process_training_job,
            str(job_uuid),
            str(bot_uuid),
            organization_id,
            [str(source_id) for source_id in selected_ids],
            job_timeout=1800,
            on_failure=Callback(training_job_failure_callback, timeout=120),
        )
        return JSONResponse(
            {
                "message": "Training queued",
                "job_id": str(job_uuid),
                "source_ids": [str(source_id) for source_id in selected_ids],
            },
            status_code=200,
        )
    except Exception as error:
        logger.exception(
            "Failed to queue training",
            extra={"job_id": str(job_uuid) if job_uuid else None},
        )
        errors = await _record_queue_failure(
            chat_db,
            dashboard_db,
            error=error,
            bot_id=bot_uuid,
            organization_id=organization_id,
            job_id=job_uuid,
            source_ids=selected_ids if selected_ids is not None else requested_ids,
        )
        return JSONResponse(
            {
                "error": errors[0]["message"],
                "errors": errors,
                "job_id": str(job_uuid) if job_uuid else None,
                "retry_available": True,
            },
            status_code=409
            if isinstance(error, TrainingFailure) and error.stage == "configuration"
            else 500,
        )


@router.get("/training/jobs/{job_id}")
async def training_job_status(
    job_id: uuid.UUID,
    request: Request,
    dashboard_db: Annotated[AsyncSession, Depends(get_async_dashboard_db)],
    chat_db: Annotated[AsyncSession, Depends(get_async_chat_db)],
):
    job = (
        await chat_db.scalars(
            select(TrainingJobs).where(
                TrainingJobs.id == job_id,
                TrainingJobs.organization_id
                == request.state.claims.get("organization_id"),
            )
        )
    ).one_or_none()
    if job is None:
        return JSONResponse({"error": "Training job not found"}, status_code=404)
    claims_bot_id = request.state.claims.get("bot_id")
    if claims_bot_id and str(job.bot_id) != str(claims_bot_id):
        return JSONResponse({"error": "Training job not found"}, status_code=404)
    sources = (
        await dashboard_db.scalars(
            select(TrainingSources).where(
                TrainingSources.id.in_([uuid.UUID(value) for value in job.source_ids]),
                TrainingSources.bot_id == job.bot_id,
                TrainingSources.organization_id == job.organization_id,
                TrainingSources.deleted_at.is_(None),
            )
        )
    ).all()
    retry_ids = [
        str(source.id) for source in sources if source.status == "training_failed"
    ]
    return {
        "job_id": str(job.id),
        "status": job.status,
        "errors": job.error_message or [],
        "retry_available": bool(retry_ids),
        "retry_source_ids": retry_ids,
        "sources": [
            {
                "id": str(source.id),
                "status": source.status,
                "errors": source.error_message or [],
            }
            for source in sources
        ],
    }


@router.delete("/training/delete/{source_id}")
async def delete_training_source(
    source_id: uuid.UUID,
    request: Request,
    chat_db: Annotated[AsyncSession, Depends(get_async_chat_db)],
):
    claims = request.state.claims
    try:
        bot_id = uuid.UUID(str(claims.get("bot_id")))
    except (ValueError, TypeError):
        return JSONResponse({"error": "Select a valid bot."}, status_code=400)
    job: TrainingJobs | None = None
    job_id: uuid.UUID | None = None
    try:
        config = (
            await chat_db.scalars(
                select(EmbeddingConfigurations).where(
                    EmbeddingConfigurations.bot_id == bot_id,
                    EmbeddingConfigurations.state == "active",
                )
            )
        ).one_or_none()
        bot_config = None
        if config is not None:
            bot_config = (
                await chat_db.scalars(
                    select(BotConfigurations).where(
                        BotConfigurations.bot_id == bot_id,
                        BotConfigurations.embedding_configuration_id == config.id,
                        BotConfigurations.state == "active",
                    )
                )
            ).one_or_none()
        if config is None or bot_config is None:
            return JSONResponse(
                {
                    "error": "This bot has no active model settings. Contact an administrator."
                },
                status_code=409,
            )
        job_id = uuid.uuid4()
        job = TrainingJobs(
            id=job_id,
            organization_id=claims.get("organization_id"),
            bot_id=bot_id,
            status="queued",
            embedding_configuration_id=config.id,
            bot_configuration_id=bot_config.id,
            source_ids=[str(source_id)],
            error_message=[],
        )
        chat_db.add(job)
        await chat_db.commit()
        Queue(connection=redis_client).enqueue(
            delete_training_source_job,
            str(job_id),
            str(source_id),
            claims.get("organization_id"),
            str(bot_id),
        )
        return JSONResponse(
            {"message": "Source removal queued", "job_id": str(job_id)}, status_code=200
        )
    except Exception as error:
        logger.exception("Could not queue source removal")
        records = error_records(
            error,
            stage="cleanup",
            job_id=str(job_id) if job_id else None,
            source_id=str(source_id),
        )
        try:
            await chat_db.rollback()
            if job_id is not None:
                persisted = (
                    await chat_db.scalars(
                        select(TrainingJobs).where(
                            TrainingJobs.id == job_id,
                            TrainingJobs.organization_id
                            == claims.get("organization_id"),
                        )
                    )
                ).one_or_none()
                if persisted is not None:
                    persisted.status = "failed"
                    persisted.error_message = append_errors(
                        persisted.error_message, records
                    )
                    persisted.completed_at = datetime.now(UTC)
                    await chat_db.commit()
        except Exception as reporting_error:
            await chat_db.rollback()
            logger.exception("Could not store source removal errors")
            records.extend(
                error_records(
                    reporting_error, stage="job", job_id=str(job_id) if job_id else None
                )
            )
        return JSONResponse(
            {
                "error": records[0]["message"],
                "errors": records,
                "job_id": str(job_id) if job_id else None,
            },
            status_code=500,
        )
