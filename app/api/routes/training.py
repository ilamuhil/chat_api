from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db, get_supabase_db
from app.models.supabase import Bots, TrainingSources


router = APIRouter()


@router.post("/api/training/queue")
async def queue_training(
    request: Request,
    db: Session = Depends(get_db),
    supabase_db: Session = Depends(get_supabase_db),
):
    # NOTE: `db` is unused for now, but kept for future "queue job" write into python_chat DB.
    _ = db

    claims = request.state.claims
    organization_id = claims.get("organization_id")
    data = await request.json()
    bot_id = data.get("bot_id")
    source_ids = data.get("source_ids", [])

    if not source_ids:
        return JSONResponse(content={"error": "Source IDs are required"}, status_code=400)

    for source_id in source_ids:
        if not isinstance(source_id, str) or not source_id.isalnum():
            return JSONResponse(content={"error": "Invalid source ID"}, status_code=400)

    bot = (
        supabase_db.query(Bots)
        .filter(Bots.id == bot_id, Bots.organization_id == organization_id)
        .first()
    )
    if not bot:
        return JSONResponse(content={"error": "Bot not found"}, status_code=404)

    sources = (
        supabase_db.query(TrainingSources)
        .filter(TrainingSources.id.in_(source_ids), TrainingSources.bot_id == bot_id)
        .all()
    )
    if len(sources) != len(source_ids):
        return JSONResponse(content={"error": "Invalid training source IDs are provided"}, status_code=400)

    # TODO: enqueue training job (python_chat DB) + kick background worker.
    return JSONResponse(content={"message": "Training queued"}, status_code=200)


