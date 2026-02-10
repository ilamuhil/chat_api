from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_chat_db, get_dashboard_db
from app.infra.redis_client import redis_client
from app.models.chat_db_models import TrainingJobs
from app.models.dashboard_db_models import TrainingSources
from app.services.worker_fns import (delete_training_source_job,
                                     process_training_job)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/training/queue")
async def queue_training(
    request: Request,
    dashboard_db: Session = Depends(get_dashboard_db),
    chat_db: Session = Depends(get_chat_db),
):
    claims = request.state.claims
    organization_id = claims.get("organization_id")

    data = await request.json()
    bot_id = data.get("bot_id")
    try:
        bot_uuid = uuid.UUID(str(bot_id))
    except Exception as e:
        logger.error("Error occurred while converting bot_id and organization_id to UUID",extra={"error": str(e)})
        return JSONResponse({"error": "Invalid bot ID"}, status_code=400)

    # ---- Concurrency guard (Python-side authority) ----
    existing_job = chat_db.scalars(select(TrainingJobs).where(TrainingJobs.bot_id == bot_uuid,TrainingJobs.status.in_(["queued","processing"])))
    
    
    if existing_job.first():
        return JSONResponse(content={"error": "Training already in progress for this bot"},status_code=409)

    # Under what statuses should the training source be considered not to be retried for training ?
    # TODO: Once the happy flow is complete, findout the places where failure is non retryable and then add those as Statuses where we skip fetching the sources for training
    
    #Fetch training sources 
    sources = dashboard_db.scalars(select(TrainingSources).where(TrainingSources.bot_id == bot_uuid,
                                                                 TrainingSources.organization_id == organization_id, TrainingSources.status.in_(["created"]),TrainingSources.deleted_at.is_(None))).all()
    
    if not sources:
        return JSONResponse(content={"message": "No files or urls that can be trained for this bot"},status_code=200)
    source_uuids = [source.id for source in sources]
    # ---- Create training job ----
    
    job = TrainingJobs(
            id=uuid.uuid4(),
            organization_id=organization_id,
            bot_id=bot_uuid,
            status="queued",
        )
        

        # ---- Enqueue Redis job ----
    try:
        chat_db.add(job)   
        chat_db.commit()
        chat_db.refresh(job)
        queue = Queue(connection=redis_client)
        queue.enqueue(
                process_training_job,
                str(job.id),
                str(bot_uuid),
                organization_id,
                [str(s) for s in source_uuids],
        )
        for source in sources:
                source.status = "queued_for_training"    
        dashboard_db.commit()
        return JSONResponse(content={"message": "Training queued", "job_id": str(job.id), "source_ids": [str(s) for s in source_uuids]}, status_code=200)
    except Exception as e:
            logger.error(
                "Failed to enqueue Redis job",
                extra={"job_id": str(job.id), "error": str(e)},
            )    
            try:
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.now(timezone.utc)
                chat_db.commit()
                dashboard_db.rollback()
                logger.info(f"Job status updated to failed: {job.id}")
                return JSONResponse(content={"message": "An error occurred while training the sources"}, status_code=500)
            except Exception as e:
                logger.error(
                    "Failed to update job status as failed",
                    extra={"job_id": str(job.id), "error": str(e)},
                )
                chat_db.rollback()
                dashboard_db.rollback()
                return JSONResponse(content={"Internal Server Error"}, status_code=500)
            

@router.delete('/api/training/delete/{source_id}')
async def delete_training_source(
    request: Request,
    chat_db: Session = Depends(get_chat_db),
):
    claims: dict = request.state.claims
    source_id : str = request.path_params.get("source_id") or ""
    if not source_id:
        logger.error("Invalid Training Source ID",
                     extra={"source_id": source_id})
        return JSONResponse(content={"error": "Invalid Training Source Provided"}, status_code=400)
    try:
        bot_id = claims.get("bot_id")
        try:
            bot_uuid = uuid.UUID(str(bot_id))
        except Exception:
            return JSONResponse(content={"error": "Invalid Bot selected"}, status_code=400)

        job = TrainingJobs(
            id=uuid.uuid4(),
            organization_id=claims.get("organization_id"),
            bot_id=bot_uuid,
            status="queued"
        )
        chat_db.add(job)
        chat_db.commit()
        logger.info("Deletion job record added to database", extra={"job_id": str(job.id)})
        chat_db.refresh(job)
    except Exception as e:
        logger.exception("Failed to queue deletion workflow job",
                        extra={"error": str(e)})
        try:
            job.status = "failed"
            job.error_message = str(e)
            job.completed_at = datetime.now(timezone.utc)
            chat_db.commit()
            logger.error("Could not add job record to database", extra={"error": str(e)})
            return JSONResponse(content={"message": "Source was deleted successfully"},status_code=200)
        except Exception as err:
            logger.exception("Failed to update job status as failed",
                            extra={"error": str(err)})
            chat_db.rollback()
            return JSONResponse(content={"message": "Source was deleted successfully"},status_code=200)
    queue = Queue(connection=redis_client)
    queue.enqueue(
        delete_training_source_job,
        str(job.id),
        str(source_id),
        claims.get("organization_id"),
        str(bot_uuid),
    )
    return JSONResponse(content={"message": "Source was deleted successfully", "job_id": str(job.id)}, status_code=200)
