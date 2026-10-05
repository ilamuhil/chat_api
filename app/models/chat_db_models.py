from __future__ import annotations

import datetime
import uuid
from typing import Any

from pgvector.sqlalchemy.vector import VECTOR
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Chat database maintained by the python chat server.


class Base(DeclarativeBase):
    """Declarative base for Chat DB ORM models."""

    pass


class Documents(Base):
    """Indexed knowledge-unit document stored for retrieval.

    Attributes:
        id: Document identifier.
        organization_id: Owning organization identifier.
        bot_id: Owning bot identifier.
        source_id: Training source identifier.
        chunk_index: Stable order of the chunk within its source.
        content: Knowledge-unit text.
        token_count: Token count used during ingestion.
        is_active: Whether the document can be retrieved.
        deleted_at: Soft-deletion timestamp.
        embedding_configuration_id: Configuration used to create the chunk.
        metadata_json: Source and structure metadata, including heading paths.
        search_vector: Generated weighted full-text search vector.
        embeddings: Related vector embedding, when available.
    """

    __tablename__ = "documents"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="documents_pk"),
        UniqueConstraint(
            "source_id",
            "embedding_configuration_id",
            "chunk_index",
            name="uq_documents_source_chunk_config_version",
        ),
        Index(
            "documents_bot_active_embedding_configuration_idx",
            "bot_id",
            "is_active",
            "embedding_configuration_id",
        ),
        Index(
            "documents_source_idx",
            "source_id",
        ),
        Index(
            "ix_documents_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[str] = mapped_column(Text, nullable=False)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    token_count: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool | None] = mapped_column(Boolean, server_default=text("true"))
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    embedding_configuration_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    embeddings: Mapped[Embeddings] = relationship(
        "Embeddings", back_populates="document", uselist=False
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            """
            setweight(
                to_tsvector(
                    'english',
                    coalesce(
                        metadata_json -> 'structure' ->> 'heading_paths',
                        ''
                    )
                ),
                'A'
            )
            ||
            setweight(
                to_tsvector('english', coalesce(content, '')),
                'D'
            )
            """,
            persisted=True,
        ),
        nullable=True,
    )
    # * Metadata structure will be the same as that of knowledge units generated from the data transformation pipeline
    # * table : {source:{page:int},structure:{content_type:"table",heading_paths:[str],domain:{}}
    # * csv : {source:{row:int},structure:{content_type:"row",heading_paths:[str],domain:{}}
    # * html : {source:{url:str},structure:{content_type:"html",heading_paths:[str],domain:{}}


class Messages(Base):
    """Chat message persisted for a conversation.

    Attributes:
        id: Message identifier.
        conversation_id: Conversation identifier.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
        role: Message author role.
        agent_id: Optional support-agent identifier.
        content_type: Message content type.
        content: Message text or payload.
        embedding_configuration_id: Optional embedding configuration identifier.
        bot_configuration_id: Optional bot configuration identifier.
        message_feedback: Feedback records associated with the message.
    """

    __tablename__ = "messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="messages_pk"),
        CheckConstraint(
            "role IN ('user', 'ai', 'support_agent', 'system')",
            name="messages_role_valid",
        ),
        Index(
            "messages_conversation_created_at_idx",
            "conversation_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True),
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    content_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'text'")
    )
    content: Mapped[str | None] = mapped_column(String)
    embedding_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    bot_configuration_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    message_feedback = relationship(
        "MessageFeedback",
        back_populates="message",
    )


class TrainingJobs(Base):
    """Background training or cleanup job tracked in the Chat DB.

    Attributes:
        id: Job identifier.
        organization_id: Owning organization identifier.
        bot_id: Bot being trained.
        status: Current job status.
        started_at: Processing start timestamp.
        completed_at: Completion timestamp.
        error_message: Failure details, when applicable.
        embedding_configuration_id: Embedding configuration for the job.
        bot_configuration_id: Bot configuration for the job.
    """

    __tablename__ = "training_jobs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="training_jobs_pk"),
        Index(
            "training_jobs_bot_status_idx",
            "bot_id",
            "status",
        ),
        Index(
            "training_jobs_embedding_configuration_id_idx",
            "embedding_configuration_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[str] = mapped_column(Text, nullable=False)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # queued, processing, completed, failed, cleanup_completed
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    error_message: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    embedding_configuration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
    bot_configuration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )


class Embeddings(Base):
    """Vector embedding associated with one indexed document.

    Attributes:
        id: Embedding identifier.
        document_id: Uniquely associated document identifier.
        embedding: pgvector embedding values.
        created_at: Creation timestamp.
        deleted_at: Soft-deletion timestamp.
        document: Related indexed document.
    """

    __tablename__ = "embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            ondelete="CASCADE",
            name="embeddings_document_id_fkey",
        ),
        PrimaryKeyConstraint("id", name="embeddings_pkey"),
        Index("embeddings_document_id_idx", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(1536), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    document: Mapped[Documents] = relationship("Documents", back_populates="embeddings")


class RetrievalLogs(Base):
    """Audit record for one retrieval operation.

    Attributes:
        id: Retrieval-log identifier.
        organization_id: Requesting organization identifier.
        bot_id: Queried bot identifier.
        conversation_id: Conversation identifier.
        message_id: Message that triggered retrieval.
        query: Query sent to retrieval.
        retrieved_document_ids: Documents returned by retrieval.
        similarity_scores: Cosine scores aligned with retrieved documents; NULL for keyword-only matches.
        retrieval_threshold: Minimum similarity threshold.
        retrieval_k: Maximum number of retrieved documents.
        reranker_used: Whether reranking was applied.
        embedding_configuration_id: Embedding configuration used.
        llm_configuration_id: Bot/LLM configuration used.
        reranked_document_ids: Documents after an optional reranker.
        details: Hybrid scores, prepared query, evidence selection and source references.
        created_at: Creation timestamp.
    """

    __tablename__ = "retrieval_logs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="retrieval_logs_pkey"),
        Index(
            "retrieval_logs_bot_created_at_idx",
            "bot_id",
            "created_at",
        ),
        Index(
            "retrieval_logs_configuration_id_idx",
            "embedding_configuration_id",
            "llm_configuration_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[str | None] = mapped_column(Text)
    bot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    query: Mapped[str | None] = mapped_column(Text)
    retrieved_document_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(Uuid))
    # Cosine similarities aligned with retrieved_document_ids; NULL for keyword-only matches.
    similarity_scores: Mapped[list[float | None] | None] = mapped_column(
        ARRAY(Double(53))
    )
    retrieval_threshold: Mapped[float | None] = mapped_column(Double(53))
    retrieval_k: Mapped[int | None] = mapped_column(Integer)
    reranker_used: Mapped[bool | None] = mapped_column(
        Boolean, server_default=text("false")
    )
    embedding_configuration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    llm_configuration_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reranked_document_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(Uuid))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )


class MessageFeedback(Base):
    """User feedback attached to a chat message.

    Attributes:
        id: Feedback identifier.
        message_id: Associated message identifier.
        feedback: Positive or negative feedback value.
        reason: Optional feedback explanation.
        created_at: Creation timestamp.
        message: Related message.
    """

    __tablename__ = "message_feedback"
    __table_args__ = (
        CheckConstraint(
            "feedback = ANY (ARRAY['positive'::text, 'negative'::text])",
            name="message_feedback_feedback_check",
        ),
        ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name="message_feedback_message_id_fkey"
        ),
        PrimaryKeyConstraint("id", name="message_feedback_pkey"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    feedback: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )

    message: Mapped[Messages] = relationship(
        "Messages", back_populates="message_feedback"
    )


class EmbeddingConfigurations(Base):
    """Versioned embedding configuration for a bot.

    Attributes:
        id: Configuration identifier.
        bot_id: Bot using the configuration.
        provider: Embedding provider name.
        model: Embedding model name.
        version: Provider or application version label.
        dimension: Vector dimension.
        min_chunk_tokens: Minimum allowed ingestion chunk size.
        target_chunk_tokens: Preferred ingestion chunk size.
        max_chunk_tokens: Maximum allowed ingestion chunk size.
        state: Configuration lifecycle state.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    __tablename__ = "embedding_configurations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="embedding_config_pkey"),
        CheckConstraint(
            "dimension > 0",
            name="embedding_config_dimension_positive",
        ),
        CheckConstraint(
            "state IN ('draft', 'training', 'active', 'deprecated')",
            name="embedding_config_state_valid",
        ),
        CheckConstraint(
            "min_chunk_tokens > 0",
            name="embedding_config_min_chunk_tokens_positive",
        ),
        CheckConstraint(
            "target_chunk_tokens >= min_chunk_tokens",
            name="embedding_config_target_chunk_tokens_valid",
        ),
        CheckConstraint(
            "max_chunk_tokens >= target_chunk_tokens",
            name="embedding_config_max_chunk_tokens_valid",
        ),
        Index(
            "embedding_configurations_one_active_per_bot_idx",
            "bot_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimension: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1536")
    )
    min_chunk_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    target_chunk_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_chunk_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
    )
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()"), onupdate=text("now()")
    )


# this is called bot configuration but mostly contains llm and reranker configurations
class BotConfigurations(Base):
    """Versioned retrieval, LLM, and reranker configuration for a bot.

    Attributes:
        id: Configuration identifier.
        bot_id: Bot using the configuration.
        embedding_configuration_id: Associated embedding configuration.
        provider: LLM or configuration provider.
        model: Model name.
        version: Provider or application version label.
        settings: Provider-specific settings.
        state: Configuration lifecycle state.
        retrieval_k: Number of candidates to retrieve.
        similarity_threshold: Minimum semantic similarity.
        created_by_user_id: User who created the configuration.
        created_at: Creation timestamp.
        updated_at: Last update timestamp.
    """

    __tablename__ = "bot_configurations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="bot_config_pkey"),
        CheckConstraint("retrieval_k > 0", name="bot_config_retrieval_k_positive"),
        CheckConstraint(
            "similarity_threshold >= 0.0 and similarity_threshold <= 1.0",
            name="bot_config_similarity_threshold_range",
        ),
        CheckConstraint(
            "state IN ('draft', 'training', 'active', 'deprecated')",
            name="bot_config_state_valid",
        ),
        Index(
            "bot_configurations_one_active_per_bot_idx",
            "bot_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    embedding_configuration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
    )
    retrieval_k: Mapped[int] = mapped_column(Integer, nullable=False)
    similarity_threshold: Mapped[float] = mapped_column(Double(53), nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    created_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()")
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(True), server_default=text("now()"), onupdate=text("now()")
    )
