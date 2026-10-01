import logging

from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_db_models import (
    BotConfigurations,
    Documents,
    EmbeddingConfigurations,
    Embeddings,
)
from app.rag.shared_dataclasses import RetrievalCandidate, RetrievalRequest

logger = logging.getLogger(__name__)


class HybridRetriever(BaseModel):
    async def _keyword_search(
        self,
        query: str,
        request: RetrievalRequest,
        chat_session: AsyncSession,
    ) -> list[RetrievalCandidate]:
        """Return active documents matching PostgreSQL full-text search terms."""
        if not query.strip():
            return []

        search_query = func.plainto_tsquery("english", query)
        keyword_score = func.ts_rank(
            Documents.search_vector,
            search_query,
        ).label("keyword_score")
        statement = (
            select(Documents, keyword_score)
            .where(
                Documents.organization_id == request.organization_id,
                Documents.bot_id == request.bot_id,
                Documents.embedding_configuration_id
                == request.embedding_configuration_id,
                Documents.is_active.is_(True),
                Documents.deleted_at.is_(None),
                Documents.search_vector.op("@@")(search_query),
            )
            .order_by(keyword_score.desc(), Documents.chunk_index)
            .limit(10)
        )

        try:
            result = await chat_session.execute(statement)
            rows = result.all()
        except Exception as error:
            logger.exception("Error performing keyword search", exc_info=error)
            raise ValueError("Keyword search failed.") from error

        return [
            RetrievalCandidate(
                document_id=document.id,
                source_id=document.source_id,
                content=document.content or "",
                metadata=document.metadata_json or {},
                keyword_score=float(score),
                keyword_rank=rank,
            )
            for rank, (document, score) in enumerate(rows, start=1)
        ]

    async def embed_query(self, query: str, model: str, dimensions: int) -> list[float]:
        try:
            embeddings = OpenAIEmbeddings(model=model, dimensions=dimensions)
            return await embeddings.aembed_query(query)
        except Exception as error:
            logger.exception("Failed to embed query", extra={"error": str(error)})
            raise ValueError("Failed to embed query. Please retry.") from error

    async def _semantic_search(
        self, query: str, request: RetrievalRequest, chat_session: AsyncSession
    ) -> list[RetrievalCandidate]:
        """Return active documents matching semantic similarity."""
        if not query.strip():
            return []

        statement = (
            select(EmbeddingConfigurations, BotConfigurations)
            .join(
                BotConfigurations,
                and_(
                    BotConfigurations.bot_id == EmbeddingConfigurations.bot_id,
                    BotConfigurations.embedding_configuration_id
                    == EmbeddingConfigurations.id,
                ),
            )
            .where(
                EmbeddingConfigurations.bot_id == request.bot_id,
                EmbeddingConfigurations.state == "active",
                BotConfigurations.state == "active",
            )
            .order_by(
                BotConfigurations.updated_at.desc().nullslast(),
                BotConfigurations.created_at.desc().nullslast(),
                EmbeddingConfigurations.updated_at.desc().nullslast(),
                EmbeddingConfigurations.created_at.desc().nullslast(),
            )
            .limit(1)
        )
        try:
            result = await chat_session.execute(statement)
            config_pair = result.one_or_none()
        except Exception as error:
            logger.exception(
                "Error loading active retrieval configurations",
                extra={"bot_id": str(request.bot_id)},
            )
            raise ValueError("Retrieval configuration lookup failed.") from error

        if config_pair is None:
            raise ValueError("No active embedding and bot configuration pair found.")

        embedding_configuration, bot_configuration = config_pair
        model = embedding_configuration.model
        dimensions = embedding_configuration.dimension
        similarity_threshold = bot_configuration.similarity_threshold

        query_embedding = await self.embed_query(query, model, dimensions)
        max_distance = max(0.0, min(1.0, 1.0 - similarity_threshold))
        distance = Embeddings.embedding.cosine_distance(query_embedding)
        statement = (
            select(Embeddings, Documents, distance)
            .join(Documents, Embeddings.document_id == Documents.id)
            .where(
                Documents.organization_id == request.organization_id,
                Documents.bot_id == request.bot_id,
                Documents.embedding_configuration_id == embedding_configuration.id,
                Documents.is_active.is_(True),
                Documents.deleted_at.is_(None),
                Embeddings.deleted_at.is_(None),
                distance <= max_distance,
            )
            .order_by(distance, Documents.chunk_index)
            .limit(10)
        )
        try:
            result = await chat_session.execute(statement)
            rows = result.all()
        except Exception as error:
            logger.exception(
                "Error performing semantic search",
                extra={"bot_id": str(request.bot_id)},
            )
            raise ValueError("Semantic search failed.") from error

        return [
            RetrievalCandidate(
                document_id=document.id,
                source_id=document.source_id,
                content=document.content or "",
                metadata=document.metadata_json or {},
                semantic_score=1.0 - float(distance_value),
                semantic_rank=rank,
            )
            for rank, (_, document, distance_value) in enumerate(rows, start=1)
        ]
