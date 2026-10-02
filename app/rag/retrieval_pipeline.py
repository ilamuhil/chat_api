from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_db_models import BotConfigurations, EmbeddingConfigurations
from app.rag.context_assembler import ContextAssembler
from app.rag.hybrid_retriever import HybridRetriever
from app.rag.query_preparer import QueryPreparer
from app.rag.result_ranker import ResultRanker
from app.rag.shared_dataclasses import (
    RetrievalRequest,
    RetrievalResult,
    RetrievalResultStatus,
)


class RetrievalPipeline:
    def __init__(self, chat_session: AsyncSession):
        self.chat_session = chat_session
        self.result_ranker = ResultRanker()
        self.hybrid_retriever = HybridRetriever()

    async def get_embedding_configuration(
        self,
        embedding_configuration_id: UUID,
    ) -> EmbeddingConfigurations | None:
        result = await self.chat_session.execute(
            select(EmbeddingConfigurations).where(
                EmbeddingConfigurations.id == embedding_configuration_id
            )
        )
        return result.scalar_one_or_none()

    async def get_bot_configuration(
        self,
        bot_id: UUID,
        embedding_configuration_id: UUID,
    ) -> BotConfigurations | None:
        result = await self.chat_session.execute(
            select(BotConfigurations)
            .where(
                BotConfigurations.bot_id == bot_id,
                BotConfigurations.embedding_configuration_id
                == embedding_configuration_id,
                BotConfigurations.state == "active",
            )
            .order_by(
                BotConfigurations.updated_at.desc().nullslast(),
                BotConfigurations.created_at.desc().nullslast(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Prepare, retrieve, rank, and assemble evidence for one request."""
        embedding_configuration = await self.get_embedding_configuration(
            request.embedding_configuration_id
        )
        if embedding_configuration is None:
            raise ValueError("Embedding configuration was not found.")

        bot_configuration = await self.get_bot_configuration(
            request.bot_id,
            request.embedding_configuration_id,
        )
        if bot_configuration is None:
            raise ValueError("No active bot configuration was found.")

        settings = bot_configuration.settings or {}
        rewrite_model = str(
            settings.get("rewrite_model") or bot_configuration.model
        )
        context_model = str(
            settings.get("context_model") or bot_configuration.model
        )

        # These settings belong to this request, so keep the instances local.
        query_preparer = QueryPreparer(
            rewrite_model=rewrite_model,
            max_history_tokens=int(
                settings.get("max_history_tokens", 2_000)
            ),
            max_query_tokens=int(settings.get("max_query_tokens", 256)),
        )
        context_assembler = ContextAssembler(
            context_model=context_model,
            max_context_tokens=int(
                settings.get("max_context_tokens", 8_000)
            ),
        )

        prepared_query = await query_preparer.prepare(request)

        keyword_candidates = await self.hybrid_retriever._keyword_search(
            prepared_query.standalone_query,
            request,
            self.chat_session,
            bot_configuration.retrieval_k,
        )

        semantic_candidates = await self.hybrid_retriever._semantic_search(
            prepared_query.standalone_query,
            request,
            self.chat_session,
            bot_configuration.retrieval_k,
            embedding_configuration=embedding_configuration,
            similarity_threshold=bot_configuration.similarity_threshold,
        )

        ranked_candidates = self.result_ranker.rank(
            keyword_candidates,
            semantic_candidates,
        )
        context_bundle = context_assembler.assemble(ranked_candidates)

        if ranked_candidates and not context_bundle.selected_candidates:
            raise ValueError(
                "Retrieved candidates do not fit the context token budget."
            )

        has_evidence = bool(context_bundle.selected_candidates)

        return RetrievalResult(
            status=(
                RetrievalResultStatus.READY
                if has_evidence
                else RetrievalResultStatus.NO_EVIDENCE
            ),
            prepared_query=prepared_query,
            context_bundle=context_bundle if has_evidence else None,
            diagnostics={
                "keyword_candidate_count": len(keyword_candidates),
                "semantic_candidate_count": len(semantic_candidates),
                "ranked_candidate_count": len(ranked_candidates),
                "embedding_configuration_id": str(
                    embedding_configuration.id
                ),
                "retrieval_k": bot_configuration.retrieval_k,
                "similarity_threshold": (
                    bot_configuration.similarity_threshold
                ),
            },
            clarification_question=None,
        )