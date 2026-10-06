from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.edu_agent import build_agent_prompt
from app.domain import ChatSession, InstituteContext
from app.models.chat_db_models import (
    BotConfigurations,
    Documents,
    EmbeddingConfigurations,
)
from app.rag import RetrievalPipeline
from app.rag.context_assembler import ContextAssembler
from app.rag.hybrid_retriever import HybridRetriever
from app.rag.query_preparer import QueryPreparer
from app.rag.result_ranker import ResultRanker
from app.rag.shared_dataclasses import (
    ConversationTurn,
    PreparedQuery,
    RetrievalCandidate,
    RetrievalFailure,
    RetrievalRequest,
    RetrievalResult,
    RetrievalResultStatus,
)
from app.services.chat import (
    _persist_retrieval_log,
    _retrieval_history,
    respond_with_ai,
)


def request(**overrides):
    values = dict(
        original_message="What are the fees for CS101?",
        organization_id="org-test",
        bot_id=uuid.uuid4(),
        embedding_configuration_id=uuid.uuid4(),
        bot_configuration_id=uuid.uuid4(),
        scoped_conversation_history=[],
    )
    values.update(overrides)
    return RetrievalRequest(**values)


def candidate(number, **values):
    return RetrievalCandidate(
        document_id=uuid.UUID(int=number),
        source_id=uuid.UUID(int=100 + number),
        content=values.pop("content", "CS101 fees are INR 10,000."),
        metadata=values.pop(
            "metadata",
            {
                "source": {"filename": "Prospectus.pdf", "pages": [2, 3]},
                "structure": {"heading_paths": ["Programs", "CS101"]},
            },
        ),
        **values,
    )


def result(status=RetrievalResultStatus.READY):
    context = None
    diagnostics = {}
    if status == RetrievalResultStatus.READY:
        item = candidate(1, keyword_rank=1)
        with patch(
            "app.rag.context_assembler.count_tokens",
            side_effect=lambda text, _: len(text.split()),
        ):
            context = ContextAssembler(
                context_model="gpt-test", max_context_tokens=100
            ).assemble(ResultRanker().rank([item], []))
        diagnostics = {
            "candidates": [
                {"document_id": str(item.document_id), "semantic_score": None}
            ]
        }
    return RetrievalResult(
        status=status,
        prepared_query=PreparedQuery(
            original_message="And fees?",
            standalone_query="CS101 fees?",
            did_rewrite=True,
        ),
        context_bundle=context,
        diagnostics=diagnostics,
    )


class RankingAndContextTests(unittest.TestCase):
    def test_overlap_is_promoted_and_scores_are_not_mixed(self):
        keyword = [
            candidate(1, keyword_rank=1, keyword_score=0.6),
            candidate(2, keyword_rank=2, keyword_score=0.3),
        ]
        semantic = [
            candidate(3, semantic_rank=1, semantic_score=0.95),
            candidate(2, semantic_rank=2, semantic_score=0.8),
        ]
        ranked = ResultRanker().rank(keyword, semantic)
        self.assertEqual(len(ranked), 3)
        self.assertEqual(ranked[0].candidate.document_id, uuid.UUID(int=2))
        self.assertEqual(ranked[0].candidate.keyword_score, 0.3)
        self.assertEqual(ranked[0].candidate.semantic_score, 0.8)
        self.assertIsNone(
            next(
                item for item in ranked if item.candidate.document_id.int == 1
            ).candidate.semantic_score
        )
        self.assertEqual([item.final_rank for item in ranked], [1, 2, 3])

    def test_budget_counts_citation_headers_and_skips_oversize_content(self):
        ranked = ResultRanker().rank(
            [
                candidate(1, content="large " * 100, keyword_rank=1),
                candidate(2, keyword_rank=2),
            ],
            [],
        )
        with patch(
            "app.rag.context_assembler.count_tokens",
            side_effect=lambda text, _: len(text.split()),
        ):
            bundle = ContextAssembler(
                context_model="gpt-test", max_context_tokens=30
            ).assemble(ranked)
        self.assertLessEqual(bundle.token_count, 30)
        self.assertEqual(len(bundle.selected_candidates), 1)
        self.assertIn("[1] Source: Prospectus.pdf", bundle.assembled_context)
        self.assertNotIn("[2]", bundle.assembled_context)
        self.assertIn("Section: Programs > CS101", bundle.assembled_context)
        self.assertEqual([ref.page for ref in bundle.source_references], [2, 3])
        self.assertEqual(
            [ref.citation_number for ref in bundle.source_references], [1, 1]
        )

    def test_url_references_and_empty_content(self):
        ranked = ResultRanker().rank(
            [
                candidate(1, content="   ", keyword_rank=1),
                candidate(
                    2,
                    keyword_rank=2,
                    metadata={"source": {"canonical_url": "https://school.test/fees"}},
                ),
            ],
            [],
        )
        with patch("app.rag.context_assembler.count_tokens", return_value=20):
            bundle = ContextAssembler(
                context_model="gpt-test", max_context_tokens=100
            ).assemble(ranked)
        self.assertEqual(len(bundle.selected_candidates), 1)
        self.assertEqual(bundle.source_references[0].url, "https://school.test/fees")
        self.assertIsNone(bundle.source_references[0].page)

    def test_prompt_uses_current_evidence_and_guides_citations(self):
        prompt = build_agent_prompt(
            InstituteContext(bot_prefs={}, retrieval_result=result())
        )
        self.assertIn("CS101 fees are INR 10,000.", prompt)
        self.assertIn("using [1], [2]", prompt)
        self.assertIn("Earlier assistant replies", prompt)
        self.assertIn("counsellor when requested", prompt)

    def test_prompt_distinguishes_no_evidence_and_search_outage(self):
        for status, expected in [
            (RetrievalResultStatus.NO_EVIDENCE, "No supporting source evidence"),
            (RetrievalResultStatus.UNAVAILABLE, "temporarily unavailable"),
        ]:
            prompt = build_agent_prompt(
                InstituteContext(bot_prefs={}, retrieval_result=result(status))
            )
            self.assertIn(expected, prompt)
            self.assertNotIn("CS101 fees are", prompt)


class QueryAndSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_rewrite_and_original_user_message(self):
        req = request(
            original_message="And fees?",
            scoped_conversation_history=[
                ConversationTurn(role="user", content="Tell me about CS101")
            ],
        )
        with (
            patch.object(
                QueryPreparer, "_rewrite", AsyncMock(return_value="CS101 fees?")
            ) as rewrite,
            patch("app.rag.query_preparer.count_tokens", return_value=5),
        ):
            prepared = await QueryPreparer(
                rewrite_model="gpt-test", max_history_tokens=50, max_query_tokens=100
            ).prepare(req)
        self.assertEqual(prepared.original_message, "And fees?")
        self.assertEqual(prepared.standalone_query, "CS101 fees?")
        self.assertTrue(prepared.did_rewrite)
        rewrite.assert_awaited_once_with(req.scoped_conversation_history, "And fees?")

    async def test_rewriter_provider_failure_falls_back(self):
        req = request(
            scoped_conversation_history=[ConversationTurn(role="user", content="CS101")]
        )
        with (
            patch.object(
                QueryPreparer,
                "_rewrite",
                AsyncMock(side_effect=RuntimeError("provider offline")),
            ),
            patch("app.rag.query_preparer.count_tokens", return_value=5),
        ):
            prepared = await QueryPreparer(
                rewrite_model="gpt-test", max_history_tokens=50, max_query_tokens=100
            ).prepare(req)
        self.assertEqual(prepared.standalone_query, req.original_message)
        self.assertFalse(prepared.did_rewrite)
        self.assertIn("original request", prepared.fallback_reason)

    async def test_first_turn_skips_rewrite_and_history_budget_preserves_recent_turns(
        self,
    ):
        preparer = QueryPreparer(
            rewrite_model="gpt-test", max_history_tokens=5, max_query_tokens=50
        )
        with patch.object(QueryPreparer, "_rewrite", AsyncMock()) as rewrite:
            prepared = await preparer.prepare(request())
        rewrite.assert_not_awaited()
        self.assertFalse(prepared.did_rewrite)
        history = [
            ConversationTurn(role="user", content="old"),
            ConversationTurn(role="assistant", content="recent"),
        ]
        with patch("app.rag.query_preparer.count_tokens", return_value=5):
            self.assertEqual(preparer._select_history(history), history[-1:])

    async def test_rewrite_truncation_rejected(self):
        with patch("app.rag.query_preparer.ChatOpenAI") as model:
            model.return_value.ainvoke = AsyncMock(
                return_value=AIMessage(
                    content="partial query",
                    response_metadata={"finish_reason": "length"},
                )
            )
            with self.assertRaisesRegex(ValueError, "token limit"):
                await QueryPreparer(
                    rewrite_model="gpt-test", max_history_tokens=50, max_query_tokens=1
                )._rewrite([], "question")

    async def test_both_searches_are_tenant_bot_and_active_config_scoped(self):
        req = request()
        document = Documents(
            id=uuid.uuid4(), source_id=uuid.uuid4(), content="CS101", metadata_json={}
        )
        db = AsyncMock(spec=AsyncSession)
        rows = MagicMock()
        rows.all.return_value = [(document, 0.1)]
        db.execute.return_value = rows
        retriever = HybridRetriever()
        await retriever.keyword_search(req.original_message, req, db, 7)
        keyword_sql = str(
            db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
        )
        self.assertIn("quote_literal", keyword_sql)
        config = EmbeddingConfigurations(
            id=req.embedding_configuration_id,
            bot_id=req.bot_id,
            state="active",
            model="text-embedding-3-small",
            dimension=3,
        )
        with patch.object(
            HybridRetriever, "embed_query", AsyncMock(return_value=[1, 0, 0])
        ):
            semantic = await retriever.semantic_search(
                req.original_message,
                req,
                db,
                7,
                embedding_configuration=config,
                similarity_threshold=0.7,
            )
        semantic_statement = db.execute.call_args.args[0]
        semantic_sql = str(semantic_statement.compile(dialect=postgresql.dialect()))
        for sql in [keyword_sql, semantic_sql]:
            self.assertIn("documents.organization_id =", sql)
            self.assertIn("documents.bot_id =", sql)
            self.assertIn("documents.embedding_configuration_id =", sql)
            self.assertIn("documents.is_active IS true", sql)
            self.assertIn("documents.deleted_at IS NULL", sql)
        self.assertIn("embeddings.deleted_at IS NULL", semantic_sql)
        self.assertAlmostEqual(semantic[0].semantic_score, 0.9)
        self.assertIn(0.30000000000000004, semantic_statement.compile().params.values())

    async def test_invalid_config_rejected_before_provider_call(self):
        req = request()
        config = EmbeddingConfigurations(
            id=req.embedding_configuration_id, bot_id=uuid.uuid4(), state="active"
        )
        with patch.object(HybridRetriever, "embed_query", AsyncMock()) as embed:
            with self.assertRaises(ValueError):
                await HybridRetriever().semantic_search(
                    "query",
                    req,
                    AsyncMock(),
                    5,
                    embedding_configuration=config,
                    similarity_threshold=0.7,
                )
        embed.assert_not_awaited()


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_pipeline(self, keyword, semantic, budget=100):
        req = request()
        db = AsyncMock(spec=AsyncSession)
        db.scalar.side_effect = [
            EmbeddingConfigurations(
                id=req.embedding_configuration_id,
                bot_id=req.bot_id,
                state="active",
                model="text-embedding-3-small",
                dimension=3,
            ),
            BotConfigurations(
                id=req.bot_configuration_id,
                bot_id=req.bot_id,
                embedding_configuration_id=req.embedding_configuration_id,
                model="gpt-test",
                retrieval_k=5,
                similarity_threshold=0.7,
                settings={"max_context_tokens": budget},
            ),
        ]
        with (
            patch.object(
                HybridRetriever, "keyword_search", AsyncMock(return_value=keyword)
            ),
            patch.object(
                HybridRetriever, "semantic_search", AsyncMock(return_value=semantic)
            ),
            patch(
                "app.rag.context_assembler.count_tokens",
                side_effect=lambda text, _: len(text.split()),
            ),
        ):
            retrieved = await RetrievalPipeline(db).retrieve(req)
        embedding_sql = str(db.scalar.call_args_list[0].args[0])
        bot_sql = str(db.scalar.call_args_list[1].args[0])
        self.assertIn("embedding_configurations.bot_id =", embedding_sql)
        self.assertIn("embedding_configurations.state =", embedding_sql)
        self.assertIn("bot_configurations.id =", bot_sql)
        return retrieved

    async def test_pipeline_fuses_overlap_and_keeps_exact_match_evidence(self):
        retrieved = await self.run_pipeline(
            [candidate(1, keyword_rank=1), candidate(2, keyword_rank=2)],
            [candidate(2, semantic_rank=1, semantic_score=0.9)],
        )
        self.assertEqual(retrieved.status, RetrievalResultStatus.READY)
        self.assertEqual(len(retrieved.context_bundle.selected_candidates), 2)
        self.assertEqual(
            retrieved.context_bundle.selected_candidates[0].candidate.document_id.int, 2
        )
        self.assertEqual(retrieved.diagnostics["keyword_candidate_count"], 2)

    async def test_empty_or_oversize_results_have_no_evidence(self):
        for keywords, budget in [([], 100), ([candidate(1, keyword_rank=1)], 1)]:
            retrieved = await self.run_pipeline(keywords, [], budget)
            self.assertEqual(retrieved.status, RetrievalResultStatus.NO_EVIDENCE)
            self.assertIsNone(retrieved.context_bundle)

    async def test_missing_active_pair_does_not_search(self):
        db = AsyncMock(spec=AsyncSession)
        db.scalar.return_value = None
        with patch.object(HybridRetriever, "keyword_search", AsyncMock()) as search:
            with self.assertRaisesRegex(ValueError, "active trained embedding"):
                await RetrievalPipeline(db).retrieve(request())
        search.assert_not_awaited()


class ChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_scoped_to_checkpoint_excludes_tools_and_system(self):
        agent = SimpleNamespace(
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    values={
                        "messages": [
                            SystemMessage(content="hidden"),
                            HumanMessage(content="CS101"),
                            AIMessage(content="program reply"),
                            ToolMessage(content="internal", tool_call_id="tool"),
                        ]
                    }
                )
            )
        )
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        turns = await _retrieval_history(agent, config)
        agent.aget_state.assert_awaited_once_with(config)
        self.assertEqual([turn.role for turn in turns], ["user", "assistant"])
        self.assertEqual([turn.content for turn in turns], ["CS101", "program reply"])

    async def exercise_chat(self, retrieval, answer="Fees are INR 10,000. [1]"):
        req = request()
        prefs = {
            "bot_id": str(req.bot_id),
            "embedding_configuration_id": str(req.embedding_configuration_id),
            "bot_configuration_id": str(req.bot_configuration_id),
            "similarity_threshold": 0.7,
            "retrieval_k": 5,
            "llm_model": "gpt-test",
        }
        session = ChatSession(
            organization_id="org-test",
            conversation_id=str(uuid.uuid4()),
            visitor_id=str(uuid.uuid4()),
        )
        socket = AsyncMock()
        session.user_socket = socket
        agent = SimpleNamespace(
            aget_state=AsyncMock(
                return_value=SimpleNamespace(
                    values={"messages": [HumanMessage(content="CS101")]}
                )
            ),
            ainvoke=AsyncMock(
                return_value={"messages": [AIMessage(content=answer)]}
                if answer is not None
                else {"messages": []}
            ),
        )
        message_id = uuid.uuid4()
        with (
            patch("app.services.chat.create_async_chat_db_session") as factory,
            patch.object(RetrievalPipeline, "retrieve", AsyncMock()) as retrieve,
            patch("app.services.chat.log_retrieval", AsyncMock()) as log,
            patch("app.services.chat.log_message", AsyncMock()),
        ):
            factory.return_value.__aenter__ = AsyncMock(return_value=AsyncMock())
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            if isinstance(retrieval, Exception):
                retrieve.side_effect = retrieval
            else:
                retrieve.return_value = retrieval
            await respond_with_ai(
                {"message": "And fees?", "message_id": str(message_id)},
                prefs,
                session,
                agent,
            )
        self.assertEqual(log.call_args.kwargs["message_id"], message_id)
        payloads = [call.args[0] for call in socket.send_json.call_args_list]
        return agent, retrieve, log, payloads

    async def test_chat_invokes_full_pipeline_and_returns_sources(self):
        retrieved = result()
        agent, retrieve, log, payloads = await self.exercise_chat(retrieved)
        req = retrieve.call_args.args[0]
        self.assertEqual(req.original_message, "And fees?")
        self.assertEqual(req.scoped_conversation_history[0].content, "CS101")
        invocation = agent.ainvoke.call_args
        self.assertEqual(invocation.args[0]["messages"][0].content, "And fees?")
        self.assertIs(invocation.kwargs["context"].retrieval_result, retrieved)
        self.assertEqual(payloads[1]["retrieval_status"], "ready")
        self.assertEqual(payloads[1]["sources"][0]["citation_number"], 1)
        self.assertIsInstance(payloads[1]["sources"][0]["document_id"], str)
        self.assertEqual(
            [payloads[0]["is_typing"], payloads[-1]["is_typing"]], [True, False]
        )
        self.assertEqual(log.call_args.kwargs["query"], "CS101 fees?")

    async def test_no_evidence_still_allows_handover_and_search_outage_is_distinct(
        self,
    ):
        for retrieval, expected in [
            (result(RetrievalResultStatus.NO_EVIDENCE), "no_evidence"),
            (RuntimeError("provider unavailable"), "unavailable"),
        ]:
            agent, _, log, payloads = await self.exercise_chat(
                retrieval, "I can connect you with a counsellor."
            )
            agent.ainvoke.assert_awaited_once()
            self.assertEqual(payloads[1]["retrieval_status"], expected)
            self.assertEqual(payloads[1]["sources"], [])
            self.assertEqual(log.call_args.kwargs["result"].status.value, expected)

    async def test_known_pipeline_failure_is_stored_with_safe_stage_and_reason(self):
        failure = RetrievalFailure(
            "query_embedding_failed",
            "query_embedding",
            "Embedding search is temporarily unavailable.",
        )
        _, _, log, _ = await self.exercise_chat(failure)
        stored_error = log.call_args.kwargs["result"].diagnostics["error"]
        self.assertEqual(stored_error["code"], "query_embedding_failed")
        self.assertEqual(stored_error["stage"], "query_embedding")
        self.assertEqual(stored_error["message"], failure.message)

    async def test_empty_agent_result_falls_back_and_clears_typing(self):
        _, _, _, payloads = await self.exercise_chat(
            result(RetrievalResultStatus.NO_EVIDENCE), None
        )
        self.assertIn("don't have confirmed information", payloads[1]["message"])
        self.assertFalse(payloads[-1]["is_typing"])

    async def test_log_preserves_keyword_only_score_and_citation_details(self):
        req = request()
        db = AsyncMock(spec=AsyncSession)
        with patch("app.services.chat.create_async_chat_db_session") as factory:
            factory.return_value.__aenter__ = AsyncMock(return_value=db)
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            await _persist_retrieval_log(
                organization_id=req.organization_id,
                bot_id=req.bot_id,
                conversation_id=uuid.uuid4(),
                query="CS101 fees?",
                result=result(),
                retrieval_threshold=0.7,
                retrieval_k=5,
                embedding_configuration_id=req.embedding_configuration_id,
                llm_configuration_id=req.bot_configuration_id,
            )
        row = db.add.call_args.args[0]
        self.assertEqual(row.similarity_scores, [None])
        self.assertEqual(row.details["source_references"][0]["citation_number"], 1)
        self.assertEqual(row.details["selected_document_ids"], [str(uuid.UUID(int=1))])
        self.assertFalse(row.reranker_used)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
