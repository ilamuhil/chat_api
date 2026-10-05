from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_db_models import BotConfigurations, EmbeddingConfigurations
from app.rag.context_assembler import ContextAssembler
from app.rag.hybrid_retriever import HybridRetriever
from app.rag.query_preparer import QueryPreparer
from app.rag.result_ranker import ResultRanker
from app.rag.shared_dataclasses import (
    RetrievalFailure,
    RetrievalRequest,
    RetrievalResult,
    RetrievalResultStatus,
)


class RetrievalPipeline:
    def __init__(self, chat_session: AsyncSession):
        self.chat_session = chat_session
        self.hybrid_retriever = HybridRetriever()

    async def get_embedding_configuration(
        self, embedding_configuration_id: UUID, bot_id: UUID
    ) -> EmbeddingConfigurations | None:
        return await self.chat_session.scalar(
            select(EmbeddingConfigurations).where(
                EmbeddingConfigurations.id == embedding_configuration_id,
                EmbeddingConfigurations.bot_id == bot_id,
                EmbeddingConfigurations.state == "active",
            )
        )

    async def get_bot_configuration(
        self,
        bot_id: UUID,
        embedding_configuration_id: UUID,
        bot_configuration_id: UUID | None = None,
    ) -> BotConfigurations | None:
        statement = select(BotConfigurations).where(
            BotConfigurations.bot_id == bot_id,
            BotConfigurations.embedding_configuration_id == embedding_configuration_id,
            BotConfigurations.state == "active",
        )
        if bot_configuration_id is not None:
            statement = statement.where(BotConfigurations.id == bot_configuration_id)
        return await self.chat_session.scalar(
            statement.order_by(
                BotConfigurations.created_at.desc(), BotConfigurations.id
            ).limit(1)
        )

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Prepare, retrieve, fuse ranks, and assemble this turn's evidence."""
        embedding_configuration = await self.get_embedding_configuration(
            request.embedding_configuration_id, request.bot_id
        )
        if embedding_configuration is None:
            raise RetrievalFailure(
                "embedding_configuration_unavailable",
                "configuration",
                "An active trained embedding configuration is required to search this bot’s sources.",
            )
        bot_configuration = await self.get_bot_configuration(
            request.bot_id,
            request.embedding_configuration_id,
            request.bot_configuration_id,
        )
        if bot_configuration is None:
            raise RetrievalFailure(
                "bot_configuration_unavailable",
                "configuration",
                "An active bot configuration matching the trained sources is required.",
            )

        settings = bot_configuration.settings or {}
        query_preparer = QueryPreparer(
            rewrite_model=str(settings.get("rewrite_model") or bot_configuration.model),
            max_history_tokens=int(settings.get("max_history_tokens", 2_000)),
            max_query_tokens=int(settings.get("max_query_tokens", 256)),
        )
        context_assembler = ContextAssembler(
            # The actual answer model determines the evidence token budget.
            context_model=bot_configuration.model,
            max_context_tokens=int(settings.get("max_context_tokens", 8_000)),
        )
        result_ranker = ResultRanker(
            rrf_k=int(settings.get("rrf_k", 60)),
            max_candidates=bot_configuration.retrieval_k * 2,
        )
        prepared_query = await query_preparer.prepare(request)
        # An AsyncSession must not execute simultaneous queries.
        keyword_candidates = await self.hybrid_retriever.keyword_search(
            prepared_query.standalone_query,
            request,
            self.chat_session,
            bot_configuration.retrieval_k,
        )
        semantic_candidates = await self.hybrid_retriever.semantic_search(
            prepared_query.standalone_query,
            request,
            self.chat_session,
            bot_configuration.retrieval_k,
            embedding_configuration=embedding_configuration,
            similarity_threshold=bot_configuration.similarity_threshold,
        )
        ranked_candidates = result_ranker.rank(keyword_candidates, semantic_candidates)
        context_bundle = context_assembler.assemble(ranked_candidates)
        has_evidence = bool(context_bundle.selected_candidates)
        return RetrievalResult(
            status=RetrievalResultStatus.READY
            if has_evidence
            else RetrievalResultStatus.NO_EVIDENCE,
            prepared_query=prepared_query,
            context_bundle=context_bundle if has_evidence else None,
            diagnostics={
                "query_rewrite_fallback": prepared_query.fallback_reason,
                "keyword_candidate_count": len(keyword_candidates),
                "semantic_candidate_count": len(semantic_candidates),
                "ranked_candidate_count": len(ranked_candidates),
                "selected_candidate_count": len(context_bundle.selected_candidates),
                "context_tokens": context_bundle.token_count,
                "max_context_tokens": context_assembler.max_context_tokens,
                "embedding_configuration_id": str(embedding_configuration.id),
                "bot_configuration_id": str(bot_configuration.id),
                "retrieval_k": bot_configuration.retrieval_k,
                "similarity_threshold": bot_configuration.similarity_threshold,
                "fusion": "reciprocal_rank_fusion",
                "rrf_k": result_ranker.rrf_k,
                "candidates": [
                    {
                        "document_id": str(item.candidate.document_id),
                        "keyword_score": item.candidate.keyword_score,
                        "semantic_score": item.candidate.semantic_score,
                        "keyword_rank": item.candidate.keyword_rank,
                        "semantic_rank": item.candidate.semantic_rank,
                        "rrf_score": item.rrf_score,
                        "final_rank": item.final_rank,
                    }
                    for item in ranked_candidates
                ],
                "no_evidence_reason": (
                    None
                    if has_evidence
                    else "context_budget_or_empty_content"
                    if ranked_candidates
                    else "no_matches"
                ),
            },
        )
