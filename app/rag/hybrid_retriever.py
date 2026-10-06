import logging
import re

from langchain_openai import OpenAIEmbeddings
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_db_models import (
    Documents,
    EmbeddingConfigurations,
    Embeddings,
)
from app.rag.shared_dataclasses import (
    RetrievalCandidate,
    RetrievalFailure,
    RetrievalRequest,
)

logger = logging.getLogger(__name__)


VOCABULARY_RULES = (
    {
        "intent": "admission_process",
        "triggers": (
            "sign up",
            "signup",
            "register for",
            "apply for",
            "application process",
            "admission process",
            "application and enrolment process",
            "application and enrollment process",
            "enroll in",
            "enrol in",
            "secure a place",
            "secure a seat",
            "get admitted",
            "join the course",
            "join the batch",
        ),
        "match_groups": (),
        "expand_with": (
            "application",
            "admissions",
            "apply",
            "enrolment",
            "enrollment",
            "registration",
            "counselling",
            "counseling",
            "offer",
            "seat reservation",
        ),
    },
    {
        "intent": "application_documents",
        "triggers": (
            "required documents",
            "documents required",
            "which documents",
            "what documents",
            "paperwork",
            "upload documents",
            "submit documents",
            "supporting documents",
        ),
        # Also catches phrasings such as “Which exact documents must I upload?”
        "match_groups": (
            ("document", "documents", "paperwork"),
            ("upload", "submit", "provide", "send", "need", "required"),
        ),
        "expand_with": (
            "application documents",
            "admission documents",
            "upload",
            "submit",
            "marksheet",
            "completion certificate",
            "identity document",
        ),
    },
    {
        "intent": "eligibility",
        "triggers": (
            "am i eligible",
            "eligibility criteria",
            "entry requirements",
            "prerequisites",
            "qualify for",
            "can i apply",
        ),
        "match_groups": (),
        "expand_with": (
            "eligibility",
            "entry criteria",
            "admission requirements",
            "prerequisites",
        ),
    },
    {
        "intent": "fees_and_payment",
        "triggers": (
            "course fee",
            "tuition fee",
            "how much does it cost",
            "payment plan",
            "monthly installment",
            "monthly instalment",
            "emi",
            "seat reservation payment",
            "reservation fee",
            "seat reservation amount",
        ),
        "match_groups": (),
        "expand_with": (
            "total fee",
            "net fee",
            "payment plan",
            "instalment",
            "installment",
            "reservation amount",
            "seat reservation",
        ),
    },
    {
        "intent": "application_fee",
        "triggers": (
            "application fee",
            "fee to apply",
            "cost to apply",
            "is applying free",
            "charges for applying",
        ),
        "match_groups": (),
        "expand_with": (
            "application fee",
            "application charge",
            "fee to apply",
        ),
    },
    {
        "intent": "scholarship",
        "triggers": (
            "financial aid",
            "scholarship",
            "fee reduction",
            "discount on the fee",
            "reduce the course fee",
        ),
        "match_groups": (),
        "expand_with": (
            "scholarship eligibility",
            "scholarship application",
            "award",
            "fee reduction",
        ),
    },
    {
        "intent": "intake_and_schedule",
        "triggers": (
            "when does the course start",
            "when do classes start",
            "application deadline",
            "last date to apply",
            "orientation date",
            "class schedule",
            "learning mode",
        ),
        "match_groups": (),
        "expand_with": (
            "intake",
            "application deadline",
            "orientation",
            "first class",
            "campus schedule",
            "online schedule",
        ),
    },
)


def _normalize_for_matching(text: str) -> str:
    """Lowercase text and turn punctuation into spaces for phrase matching."""
    return " ".join(re.findall(r"\w+", text.casefold()))


def _contains_phrase(normalized_text: str, phrase: str) -> bool:
    normalized_phrase = _normalize_for_matching(phrase)
    return f" {normalized_phrase} " in f" {normalized_text} "


def expand_keyword_query(query: str) -> tuple[list[str], list[str]]:
    """Return matched intent names and their deduplicated expansion terms."""
    normalized_query = _normalize_for_matching(query)
    matched_intents: list[str] = []
    expansion_terms: list[str] = []

    for rule in VOCABULARY_RULES:
        direct_match = any(
            _contains_phrase(normalized_query, phrase) for phrase in rule["triggers"]
        )
        grouped_match = bool(rule["match_groups"]) and all(
            any(_contains_phrase(normalized_query, term) for term in group)
            for group in rule["match_groups"]
        )

        if direct_match or grouped_match:
            matched_intents.append(rule["intent"])
            expansion_terms.extend(rule["expand_with"])

    # Preserve order while removing duplicates.
    return matched_intents, list(dict.fromkeys(expansion_terms))


class HybridRetriever(BaseModel):
    async def keyword_search(
        self,
        query: str,
        request: RetrievalRequest,
        chat_session: AsyncSession,
        retrieval_k: int = 10,
    ) -> list[RetrievalCandidate]:
        """Return active documents matching PostgreSQL full-text search terms."""
        if not query.strip():
            return []

        if retrieval_k <= 0:
            raise ValueError("Search limit must be positive.")
        # PostgreSQL parses and escapes terms; joining lexemes with OR preserves
        # exact program names/codes without requiring every word of a question.
        matched_intents, expansion_terms = expand_keyword_query(query)
        keyword_query_text = " ".join((query, *expansion_terms))

        logger.debug(
            "Expanded keyword search query",
            extra={
                "matched_intents": matched_intents,
                "expansion_terms": expansion_terms,
            },
        )

        lexemes = func.tsvector_to_array(
            func.to_tsvector("english", keyword_query_text)
        )
        terms = func.unnest(lexemes).table_valued("lexeme").render_derived()
        expression = select(
            func.string_agg(func.quote_literal(terms.c.lexeme), " | ")
        ).scalar_subquery()
        search_query = func.to_tsquery("english", func.coalesce(expression, ""))
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
            .order_by(keyword_score.desc(), Documents.chunk_index, Documents.id)
            .limit(retrieval_k)
        )

        try:
            result = await chat_session.execute(statement)
            rows = result.all()
        except Exception as error:
            logger.exception("Error performing keyword search", exc_info=error)
            raise RetrievalFailure(
                "keyword_search_failed",
                "keyword_search",
                "Keyword reference search is temporarily unavailable. Please try again shortly.",
            ) from error

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
            embeddings = OpenAIEmbeddings(
                model=model, dimensions=dimensions, timeout=15, max_retries=1
            )
            return await embeddings.aembed_query(query)
        except Exception as error:
            logger.exception("Failed to embed query", extra={"error": str(error)})
            raise RetrievalFailure(
                "query_embedding_failed",
                "query_embedding",
                "The search request could not be processed by the embedding provider. Please try again shortly.",
            ) from error

    async def semantic_search(
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
            )
            .order_by(distance, Documents.chunk_index, Documents.id)
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
            raise RetrievalFailure(
                "semantic_search_failed",
                "semantic_search",
                "Semantic reference search is temporarily unavailable. Please try again shortly.",
            ) from error
        scored_rows = [
            (document, 1.0 - float(distance_value)) for document, distance_value in rows
        ]

        logger.info(
            "Semantic search score diagnostics",
            extra={
                "threshold": similarity_threshold,
                "top_scores": [
                    {
                        "document_id": str(document.id),
                        "score": score,
                        "passed": score >= similarity_threshold,
                    }
                    for document, score in scored_rows[:5]
                ],
                "returned_count": sum(
                    score >= similarity_threshold for _, score in scored_rows
                ),
            },
        )

        return [
            RetrievalCandidate(
                document_id=document.id,
                source_id=document.source_id,
                content=document.content or "",
                metadata=document.metadata_json or {},
                semantic_score=score,
                semantic_rank=rank,
            )
            for rank, (document, score) in enumerate(
                (row for row in scored_rows if row[1] >= similarity_threshold),
                start=1,
            )
        ]
