from __future__ import annotations
from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db.session import get_db, get_supabase_db
from app.models.supabase import Bots, TrainingSources
from app.models.python_chat import TrainingJobs
from rq import Queue
from app.infra.redis_client import redis_client
from app.services.worker_fns import process_training_job


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
    if isinstance(bot_id, str):
        bot_id = int(bot_id)
    elif not isinstance(bot_id, int):
        return JSONResponse(content={"error": "Invalid bot ID"}, status_code=400)
    
    source_ids = data.get("source_ids", [])
    if not source_ids:
        return JSONResponse(content={"error": "Source IDs are required"}, status_code=400)

    for source_id in source_ids:
        if not isinstance(source_id, str):
            return JSONResponse(content={"error": "Invalid source ID"}, status_code=400)

    try:
        bot = (
            supabase_db.query(Bots)
            .filter(Bots.id == bot_id, Bots.organization_id == organization_id)
            .first()
        )
    except Exception as e:
        # Read-only query, but keep a consistent API error response.
        supabase_db.rollback()
        return JSONResponse(content={"error": f"Failed to fetch bot: {e}"}, status_code=500)
    if not bot:
        return JSONResponse(content={"error": "Bot not found"}, status_code=404)

    try:
        sources = (
            supabase_db.query(TrainingSources)
            .filter(TrainingSources.id.in_(source_ids), TrainingSources.bot_id == bot_id)
            .all()
        )
    except Exception as e:
        supabase_db.rollback()
        return JSONResponse(content={"error": f"Failed to fetch training sources: {e}"}, status_code=500)
    if len(sources) != len(source_ids):
        return JSONResponse(content={"error": "Invalid training source IDs are provided"}, status_code=400)
    
    # * The training sources table from supabase and the training jobs table from python_chat are linked by the bot_id.
    try:
        job = TrainingJobs(
            id=uuid.uuid4(),
            organization_id=organization_id,
            bot_id=bot_id,
            status="queued"
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        queue = Queue(connection=redis_client)
        # Pass only primitives to the job (RQ pickles args/kwargs).
        queue.enqueue(process_training_job, str(job.id), bot_id, organization_id, source_ids)
    except Exception as e:
        print(f"Failed to queue training: {e}")
        db.rollback()
        return JSONResponse(content={"error": f"Failed to queue training: {e}"}, status_code=500)
    
    return JSONResponse(content={"message": "Training queued"}, status_code=200)
    
    


