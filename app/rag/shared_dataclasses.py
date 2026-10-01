from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievalResultStatus(StrEnum):
    """Status of the retrieval pipeline result.

    Members:
        READY: Retrieval completed with usable evidence.
        NEEDS_CLARIFICATION: The user query needs clarification.
        NO_EVIDENCE: Retrieval found no suitable evidence.
    """

    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_EVIDENCE = "no_evidence"


class ConversationTurn(BaseModel):
    """One message in the scoped conversation history.

    Attributes:
        role: Whether the message came from the user or the assistant.
        content: Text content of the conversation message.
    """

    role: Literal["user", "assistant"] = Field(
        ..., description="The role of the conversation turn."
    )
    content: str


class SourceReference(BaseModel):
    """Citation information for a retrieved source.

    Attributes:
        source_id: Identifier of the training source.
        document_id: Identifier of the indexed document.
        label: Human-readable source label.
        page: Optional page number containing the evidence.
        url: Optional URL containing the evidence.
    """

    source_id: UUID = Field(..., description="The id of the source of the document.")
    document_id: UUID = Field(..., description="The id of the document.")
    label: str = Field(..., description="The label of the source reference.")
    page: int | None = Field(
        default=None, description="The page number of the source reference."
    )
    url: str | None = Field(
        default=None, description="The url of the source reference."
    )


class PreparedQuery(BaseModel):
    """Original and normalized forms of a user query.

    Attributes:
        original_message: The original message received from the user.
        standalone_query: Query rewritten to be understandable without history.
        did_rewrite: Whether the standalone query differs due to rewriting.
    """

    original_message: str = Field(
        ..., description="The original message from the user."
    )
    standalone_query: str = Field(
        ..., description="The standalone query to be used for RAG retrieval."
    )
    did_rewrite: bool = Field(
        ..., description="Whether the message was rewritten by the system."
    )


class RetrievalRequest(BaseModel):
    """Input required to perform retrieval for a user message.

    Attributes:
        original_message: The original message received from the user.
        organization_id: Identifier of the user's organization.
        bot_id: Identifier of the bot being queried.
        embedding_configuration_id: Embedding configuration used for retrieval.
        scoped_conversation_history: Conversation history available to the query
            preparer.
    """

    original_message: str = Field(
        ..., description="The original message from the user."
    )
    organization_id: str = Field(
        ..., description="The id of the organization the user belongs to."
    )
    bot_id: UUID = Field(
        ..., description="The id of the bot to retrieve the information from."
    )
    embedding_configuration_id: UUID = Field(
        ...,
        description="The id of the embedding configuration to be used for RAG retrieval.",
    )
    scoped_conversation_history: list[ConversationTurn] = Field(
        ...,
        description="The scoped conversation history to be used for RAG retrieval.",
    )


class RetrievalCandidate(BaseModel):
    """A document candidate returned by keyword or semantic retrieval.

    Attributes:
        document_id: Identifier of the indexed document.
        source_id: Identifier of the source that produced the document.
        content: Text content used as retrieval evidence.
        metadata: Additional source and document metadata.
        keyword_score: Keyword-search score, when available.
        semantic_score: Semantic-similarity score, when available.
        keyword_rank: Rank from keyword retrieval, when available.
        semantic_rank: Rank from semantic retrieval, when available.
    """

    document_id: UUID = Field(
        ..., description="The id of the document to be retrieved."
    )
    source_id: UUID = Field(..., description="The id of the source of the document.")
    content: str = Field(
        ..., description="The content of the document to be retrieved."
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="The metadata of the document to be retrieved.",
    )
    keyword_score: float | None = Field(
        default=None, description="The score of the document based on the keywords."
    )
    semantic_score: float | None = Field(
        default=None,
        description="The score of the document based on the semantic similarity.",
    )
    keyword_rank: int | None = Field(
        default=None, description="The rank of the document based on the keywords."
    )
    semantic_rank: int | None = Field(
        default=None,
        description="The rank of the document based on the semantic similarity.",
    )


class RankedCandidate(BaseModel):
    """Retrieval candidate after rank fusion and optional reranking.

    Attributes:
        rrf_score: Reciprocal Rank Fusion score.
        rrf_rank: Rank assigned by Reciprocal Rank Fusion.
        candidate: Original retrieval candidate.
        reranker_score: Optional score from a reranking model.
        final_rank: Final rank after all ranking stages.
    """

    rrf_score: float = Field(..., description="The RRF score of the document.")
    rrf_rank: int | None = Field(
        default=None, description="The rank of the document based on the RRF score."
    )
    candidate: RetrievalCandidate = Field(..., description="The candidate document.")
    reranker_score: float | None = Field(
        default=None, description="The score of the document based on the reranker."
    )
    final_rank: int | None = Field(
        default=None,
        description="The final rank of the document in the retrieval results.",
    )


class ContextBundle(BaseModel):
    """Evidence selected and formatted for the answer-generation model.

    Attributes:
        assembled_context: Final text context supplied to the language model.
        selected_candidates: Ranked candidates included in the context.
        source_references: Citations corresponding to the selected evidence.
        token_count: Number of tokens in the assembled context.
        evidence_notes: Notes explaining evidence selection or limitations.
    """

    assembled_context: str = Field(
        ..., description="The assembled context of the document."
    )
    selected_candidates: list[RankedCandidate] = Field(
        ..., description="The selected candidates from the retrieval results."
    )
    source_references: list[SourceReference] = Field(
        ..., description="The source references of the document."
    )
    token_count: int = Field(
        ..., description="The token count of the assembled context."
    )
    evidence_notes: list[str] = Field(
        ..., description="The evidence notes of the assembled context."
    )


class RetrievalResult(BaseModel):
    """Complete result returned by the retrieval pipeline.

    Attributes:
        status: Overall retrieval outcome.
        prepared_query: Original and standalone query forms.
        context_bundle: Selected evidence, if evidence was found.
        diagnostics: Operational and ranking details for debugging.
        clarification_question: Question to ask when clarification is needed.
    """

    status: RetrievalResultStatus = Field(
        ..., description="The status of the retrieval result."
    )
    prepared_query: PreparedQuery = Field(
        ..., description="The prepared query of the retrieval result."
    )
    context_bundle: ContextBundle | None = Field(
        None, description="The context bundle of the retrieval result."
    )
    diagnostics: dict[str, Any] = Field(
        ..., description="The diagnostics of the retrieval result."
    )
    clarification_question: str | None = Field(
        None, description="The clarification question of the retrieval result."
    )
