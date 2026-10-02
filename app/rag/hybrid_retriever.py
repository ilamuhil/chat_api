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
        retrieval_k: int = 10,
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
            .limit(retrieval_k)
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
    self,
    query: str,
    request: RetrievalRequest,
    chat_session: AsyncSession,
    limit: int,
    *,
    embedding_configuration: EmbeddingConfigurations,
    similarity_threshold: float,
) -> list[RetrievalCandidate]:
        """Return active documents ranked by semantic similarity."""
        if not query.strip():
            return []

        if limit <= 0:
            raise ValueError("Search limit must be positive.")

        if (
            embedding_configuration.id != request.embedding_configuration_id
            or embedding_configuration.bot_id != request.bot_id
            or embedding_configuration.state != "active"
        ):
            raise ValueError("Invalid or inactive embedding configuration.")

        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("Similarity threshold must be between 0 and 1.")

        query_embedding = await self.embed_query(
            query,
            embedding_configuration.model,
            embedding_configuration.dimension,
        )

        distance = Embeddings.embedding.cosine_distance(query_embedding)
        max_distance = 1.0 - similarity_threshold

        statement = (
            select(Documents, distance.label("distance"))
            .join(Embeddings, Embeddings.document_id == Documents.id)
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
            .limit(limit)
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
            for rank, (document, distance_value) in enumerate(rows, start=1)
        ]