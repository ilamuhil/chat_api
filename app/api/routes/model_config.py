import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import create_async_dashboard_db_session, get_async_chat_db
from app.infra.local_cache import configuration_cache
from app.models.chat_db_models import BotConfigurations, EmbeddingConfigurations
from app.models.dashboard_db_models import Bots, OrganizationMembers
from app.services.billing import STANDARD_MODEL


class ModelConfigCreate(BaseModel):
    bot_id: str = Field(
        ..., description="The id of the bot to create the model config for"
    )
    user_id: str = Field(
        ..., description="Id of the admin user that is creating the bot"
    )


class ModelConfigCreateResponse(BaseModel):
    config_id: uuid.UUID = Field(
        ..., description="The id of the model config that was created"
    )


logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/model-config/create", response_model=ModelConfigCreateResponse)
async def create_model_config(
    body: ModelConfigCreate,
    request: Request,
    chat_db: Annotated[AsyncSession, Depends(get_async_chat_db)],
):
    if not body.bot_id:
        logger.error("Bot Id was not provided by Dashboard server")
        return JSONResponse(
            content={"error": "Bot Id was not provided by Dashboard server"},
            status_code=400,
        )
    if not body.user_id:
        logger.error("User Id was not provided by Dashboard server")
        return JSONResponse(
            content={"error": "User Id was not provided by Dashboard server"},
            status_code=400,
        )

    try:
        bot_uuid = uuid.UUID(body.bot_id)
        user_uuid = uuid.UUID(body.user_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="bot_id and user_id must be valid UUIDs",
        ) from None

    claims = request.state.claims
    # Administrative operations must be authorized by the signed identity,
    # never by a user_id supplied in the request body.
    if claims.get("type") != "dashboard_admin":
        raise HTTPException(status_code=403, detail="Administrator token required")
    try:
        authenticated_user_id = uuid.UUID(str(claims.get("user_id")))
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(status_code=403, detail="Administrator identity required") from None
    if authenticated_user_id != user_uuid:
        raise HTTPException(status_code=403, detail="User identity mismatch")

    organization_id = request.state.organization_id
    async with create_async_dashboard_db_session() as dashboard:
        admin = await dashboard.scalar(
            select(OrganizationMembers.id).where(
                OrganizationMembers.organization_id == organization_id,
                OrganizationMembers.user_id == user_uuid,
                OrganizationMembers.role == "admin",
            )
        )
        if admin is None:
            raise HTTPException(
                status_code=403, detail="Organization administrator required"
            )
        owned_bot = await dashboard.scalar(
            select(Bots.id).where(
                Bots.id == bot_uuid,
                Bots.organization_id == organization_id,
            )
        )
    if owned_bot is None:
        raise HTTPException(status_code=404, detail="Bot not found")

    try:
        embedding_configuration = EmbeddingConfigurations(
            bot_id=bot_uuid,
            provider="openai",
            model="text-embedding-3-small",
            version="1",
            dimension=1536,
            min_chunk_tokens=100,
            max_chunk_tokens=700,
            target_chunk_tokens=400,
            state="draft",
        )
        chat_db.add(embedding_configuration)
        await chat_db.flush()

        bot_configuration = BotConfigurations(
            bot_id=bot_uuid,
            embedding_configuration_id=embedding_configuration.id,
            provider="openai",
            model=STANDARD_MODEL,
            version="1",
            retrieval_k=10,
            similarity_threshold=0.7,
            created_by_user_id=user_uuid,
            state="draft",
        )
        chat_db.add(bot_configuration)
        await chat_db.flush()
        config_id = bot_configuration.id
        await chat_db.commit()
        configuration_cache.invalidate_bot(str(bot_uuid))
        return ModelConfigCreateResponse(config_id=config_id)
    except IntegrityError:
        await chat_db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Model configuration could not be created because of a database conflict",
        ) from None
    except Exception as error:
        await chat_db.rollback()
        logger.exception("Unexpected error creating model configuration")
        raise HTTPException(
            status_code=500,
            detail="Internal server error",
        ) from error
