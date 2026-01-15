from __future__ import annotations
from datetime import datetime, timezone
import uuid
import logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db.session import get_db, get_supabase_db
from app.models.supabase import Bots, TrainingSources
from app.models.python_chat import TrainingJobs
from rq import Queue
from app.infra.redis_client import redis_client
from app.services.worker_fns import process_training_job, delete_training_source_job

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/training/queue")
async def queue_training(
    request: Request,
    db: Session = Depends(get_db),
    supabase_db: Session = Depends(get_supabase_db),
):
    claims = request.state.claims
    organization_id = claims.get("organization_id")

    data = await request.json()
    bot_id = data.get("bot_id")

    # bot_id is INTEGER (as clarified)
    if isinstance(bot_id, str):
        try:
            bot_id = int(bot_id)
        except ValueError:
            return JSONResponse({"error": "Invalid bot ID"}, status_code=400)
    elif not isinstance(bot_id, int):
        return JSONResponse({"error": "Invalid bot ID"}, status_code=400)

    source_ids = data.get("source_ids", [])
    if not source_ids or not isinstance(source_ids, list):
        return JSONResponse({"error": "Source IDs are required"}, status_code=400)

    for source_id in source_ids:
        if not isinstance(source_id, str):
            return JSONResponse({"error": "Invalid source ID"}, status_code=400)

    # ---- Validate bot ownership ----
    bot = (
        supabase_db.query(Bots)
        .filter(
            Bots.id == bot_id,
            Bots.organization_id == organization_id,
        )
        .first()
    )
    if not bot:
        return JSONResponse({"error": "Bot not found"}, status_code=404)

    # ---- Concurrency guard (Python-side authority) ----
    existing_job = (
        db.query(TrainingJobs)
        .filter(
            TrainingJobs.bot_id == bot_id,
            TrainingJobs.status.in_(["queued", "running"]),
        )
        .first()
    )
    if existing_job:
        return JSONResponse(
            {"error": "Training already in progress for this bot"},
            status_code=409,
        )

    # ---- Validate training sources (must be pending) ----
    sources = (
        supabase_db.query(TrainingSources)
        .filter(
            TrainingSources.id.in_(source_ids),
            TrainingSources.bot_id == bot_id,
            TrainingSources.status == "pending",
        )
        .all()
    )

    if len(sources) != len(source_ids):
        return JSONResponse(
            {"error": "Invalid or non-pending training source IDs"},
            status_code=400,
        )

    # ---- Create training job ----
    try:
        job = TrainingJobs(
            id=uuid.uuid4(),
            organization_id=organization_id,
            bot_id=bot_id,
            status="queued",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        # ---- Enqueue Redis job ----
        try:
            queue = Queue(connection=redis_client)
            queue.enqueue(
                process_training_job,
                str(job.id),
                bot_id,
                organization_id,
                source_ids,
            )
        except Exception as e:
            logger.error(
                "Failed to enqueue Redis job",
                extra={"job_id": str(job.id), "error": str(e)},
            )
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(job)
            return JSONResponse(
                {"error": "Failed to enqueue training job"},
                status_code=500,
            )

    except Exception as e:
        logger.error(
            "Failed to queue training",
            extra={"bot_id": bot_id, "error": str(e)},
        )
        db.rollback()
        return JSONResponse(
            {"error": "Failed to queue training"},
            status_code=500,
        )

    return JSONResponse(
        {
            "message": "Training queued",
            "job_id": str(job.id),
            "source_count": len(source_ids),
        },
        status_code=200,
    )



@router.delete('/api/training/delete/{source_id}')
async def delete_training_source(request: Request, db: Session = Depends(get_db)):
    claims: dict = request.state.claims
    source_id = request.path_params.get("source_id")
    if not source_id:
        logger.error("Invalid Training Source ID",
                     extra={"source_id": source_id})
        return JSONResponse(content={"error": "Invalid Training Source Provided"}, status_code=400)
    try:
        job = TrainingJobs(
            id=uuid.uuid4(),
            organization_id=claims.get("organization_id"),
            bot_id=claims.get("bot_id"),
            status="queued"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
    except Exception as e:
        logger.critical("Failed to queue deletion workflow job",
                        extra={"error": str(e)})
        return JSONResponse(content={"error": f"Failed to queue deletion workflow job: {e}"}, status_code=500)
    queue = Queue(connection=redis_client)
    queue.enqueue(delete_training_source_job, source_id, str(
        job.id), claims.get("organization_id"), claims.get("bot_id"))
    return JSONResponse(content={"message": "Deletion workflow queued", "job_id": str(job.id)}, status_code=200)
