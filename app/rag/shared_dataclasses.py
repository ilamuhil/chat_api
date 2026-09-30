from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RetrievalResultStatus(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    NO_EVIDENCE = "no_evidence"


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"] = Field(
        ..., description="The role of the conversation turn."
    )
    content: str


class SourceReference(BaseModel):
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
    document_id: UUID = Field(
        ..., description="The id of the document to be retrieved."
    )
    source_id: UUID = Field(..., description="The id of the source of the document.")
    section_title: str = Field(
        ..., description="The title of the section of the document to be retrieved."
    )
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
