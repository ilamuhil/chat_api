"""Opt-in PostgreSQL/pgvector verification; all fixture writes roll back.

Run with RUN_POSTGRES_RETRIEVAL_TESTS=1 against the configured Chat DB.
Embedding calls are mocked; SQL search, configuration lookup and assembly are real.
"""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.db.session import async_chat_engine, create_async_chat_db_session
from app.models.chat_db_models import (
    BotConfigurations,
    Documents,
    EmbeddingConfigurations,
    Embeddings,
    RetrievalLogs,
)
from app.rag import RetrievalPipeline, RetrievalRequest, RetrievalResultStatus
from app.rag.hybrid_retriever import HybridRetriever


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_RETRIEVAL_TESTS") == "1", "Opt-in PostgreSQL test"
)
class PostgresRetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await async_chat_engine.dispose()

    async def test_scoped_hybrid_search_and_json_audit_roundtrip(self):
        bot_id, embedding_id, config_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        org = f"retrieval-test-{uuid.uuid4()}"
        vector = [1.0] + [0.0] * 1535
        orthogonal = [0.0, 1.0] + [0.0] * 1534
        async with create_async_chat_db_session() as db:
            try:
                db.add(
                    EmbeddingConfigurations(
                        id=embedding_id,
                        bot_id=bot_id,
                        provider="openai",
                        model="text-embedding-3-small",
                        dimension=1536,
                        state="active",
                        min_chunk_tokens=1,
                        target_chunk_tokens=100,
                        max_chunk_tokens=200,
                    )
                )
                db.add(
                    BotConfigurations(
                        id=config_id,
                        bot_id=bot_id,
                        embedding_configuration_id=embedding_id,
                        provider="openai",
                        model="gpt-5.6-luna",
                        state="active",
                        retrieval_k=10,
                        similarity_threshold=0.7,
                        settings={"max_context_tokens": 1000},
                    )
                )
                expected, excluded = [], []
                for kind, content, document_vector in [
                    (
                        "keyword",
                        "CS101 fees are INR 10,000. O'Reilly materials included.",
                        orthogonal,
                    ),
                    ("semantic", "Tuition costs ten thousand rupees.", vector),
                    ("overlap", "CS101 fees include materials.", vector),
                    (
                        "other_org",
                        "CS101 fees private to another organization.",
                        vector,
                    ),
                    ("other_bot", "CS101 fees private to another bot.", vector),
                    ("other_config", "CS101 fees from an old configuration.", vector),
                    ("inactive", "CS101 fees not trained.", vector),
                    ("deleted_document", "CS101 fees deleted.", vector),
                    ("deleted_vector", "Unrelated text with deleted vector.", vector),
                ]:
                    doc = Documents(
                        id=uuid.uuid4(),
                        organization_id=org if kind != "other_org" else "other-org",
                        bot_id=bot_id if kind != "other_bot" else uuid.uuid4(),
                        embedding_configuration_id=embedding_id
                        if kind != "other_config"
                        else uuid.uuid4(),
                        source_id=uuid.uuid4(),
                        chunk_index=0,
                        content=content,
                        is_active=kind != "inactive",
                        deleted_at=datetime.now(UTC)
                        if kind == "deleted_document"
                        else None,
                        metadata_json={
                            "source": {"filename": f"{kind}.pdf", "page": 2},
                            "structure": {"heading_paths": ["Tuition"]},
                        },
                    )
                    db.add(doc)
                    await db.flush()
                    db.add(
                        Embeddings(
                            document_id=doc.id,
                            embedding=document_vector,
                            deleted_at=datetime.now(UTC)
                            if kind == "deleted_vector"
                            else None,
                        )
                    )
                    (
                        expected
                        if kind in {"keyword", "semantic", "overlap"}
                        else excluded
                    ).append(doc.id)
                await db.flush()
                req = RetrievalRequest(
                    original_message="What are the CS101 fees? O'Reilly",
                    organization_id=org,
                    bot_id=bot_id,
                    embedding_configuration_id=embedding_id,
                    bot_configuration_id=config_id,
                    scoped_conversation_history=[],
                )
                with patch.object(
                    HybridRetriever, "embed_query", AsyncMock(return_value=vector)
                ):
                    result = await RetrievalPipeline(db).retrieve(req)
                    # Apostrophes, colon syntax, punctuation and stopwords must
                    # remain search data, rather than breaking tsquery parsing.
                    for query in [
                        "O'Reilly's CS101",
                        "CS101:* fees",
                        "and the",
                        "'quoted' - +:",
                    ]:
                        await HybridRetriever().keyword_search(query, req, db, 10)
                self.assertEqual(result.status, RetrievalResultStatus.READY)
                ids = [
                    item.candidate.document_id
                    for item in result.context_bundle.selected_candidates
                ]
                self.assertEqual(set(ids), set(expected))
                self.assertFalse(set(ids) & set(excluded))
                self.assertEqual(ids[0], expected[2])
                self.assertLessEqual(result.context_bundle.token_count, 1000)
                self.assertEqual(len(result.context_bundle.source_references), 3)
                log = RetrievalLogs(
                    id=uuid.uuid4(),
                    organization_id=org,
                    bot_id=bot_id,
                    retrieved_document_ids=ids,
                    similarity_scores=[
                        item.candidate.semantic_score
                        for item in result.context_bundle.selected_candidates
                    ],
                    details=result.model_dump(mode="json", exclude={"context_bundle"}),
                )
                db.add(log)
                await db.flush()
                log_id = log.id
                db.expire(log)
                stored = await db.scalar(
                    select(RetrievalLogs).where(RetrievalLogs.id == log_id)
                )
                self.assertEqual(stored.details["status"], "ready")
                self.assertIn(None, stored.similarity_scores)
                print(
                    "PostgreSQL hybrid search, tenant exclusions, quoted terms, citations and JSONB audit verified; fixtures rolled back."
                )
            finally:
                await db.rollback()
