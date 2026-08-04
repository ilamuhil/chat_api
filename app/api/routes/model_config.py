import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_chat_db
from app.models.chat_db_models import BotConfigurations, EmbeddingConfigurations


class ModelConfigCreate(BaseModel):
  bot_id: str = Field(..., description="The id of the bot to create the model config for")
  user_id: str = Field(..., description="Id of the admin user that is creating the bot")
  
class ModelConfigCreateResponse(BaseModel):
    config_id: uuid.UUID = Field(..., description="The id of the model config that was created")

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/api/model-config/create",response_model=ModelConfigCreateResponse)
async def create_model_config(request: ModelConfigCreate, chat_db: Session = Depends(get_chat_db)):
  if not request.bot_id:
    logger.error("Bot Id was not provided by Dashboard server")
    return JSONResponse(content={"error": "Bot Id was not provided by Dashboard server"}, status_code=400)
  if not request.user_id:
    logger.error("User Id was not provided by Dashboard server")
    return JSONResponse(content={"error": "User Id was not provided by Dashboard server"}, status_code=400)

  try:
    bot_uuid = uuid.UUID(request.bot_id)
    user_uuid = uuid.UUID(request.user_id)
    embedding_configuration = EmbeddingConfigurations(
      bot_id=bot_uuid,
      provider="openai",
      model="text-embedding-3-small",
      version="1",
      dimension=1536,
      chunk_size=800,
      chunk_overlap=100,
      state="draft",
    )
    chat_db.add(embedding_configuration)
    chat_db.flush()

    bot_configuration = BotConfigurations(
      bot_id=bot_uuid,
      embedding_configuration_id=embedding_configuration.id,
      provider="openai",
      model="gpt-5.6-luna",
      version="1",
      retrieval_k=10,
      similarity_threshold=0.7,
      created_by_user_id=user_uuid,
      state="draft",
    )
    chat_db.add(bot_configuration)
    chat_db.commit()
    return ModelConfigCreateResponse(config_id=bot_configuration.id)
  except ValueError:
    chat_db.rollback()
    raise HTTPException(
        status_code=400,
        detail="bot_id and user_id must be valid UUIDs",
    )
  except IntegrityError:
    chat_db.rollback()
    raise HTTPException(
        status_code=409,
        detail="Model configuration could not be created because of a database conflict",
    )
  except Exception:
    chat_db.rollback()
    logger.exception("Unexpected error creating model configuration")
    raise HTTPException(
        status_code=500,
        detail="Internal server error",
    )