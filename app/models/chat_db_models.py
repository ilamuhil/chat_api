from __future__ import annotations

import datetime
import uuid
from typing import Optional

from pgvector.sqlalchemy.vector import VECTOR
from sqlalchemy import (ARRAY, Boolean, CheckConstraint, DateTime, Double,
                        Float, ForeignKeyConstraint, Index, Integer,
                        PrimaryKeyConstraint, String, Text, UniqueConstraint,
                        Uuid, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#Chat database maintained by the python chat server.




class Base(DeclarativeBase):
    pass


class Documents(Base):
    __tablename__ = "documents"
    __table_args__ = (PrimaryKeyConstraint("id", name="documents_pk"),UniqueConstraint(
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
        ))
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    organization_id: Mapped[str] = mapped_column(Text, nullable=False)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text('now()'))
    section_title: Mapped[Optional[str]] = mapped_column(Text)
    token_count: Mapped[Optional[int]] = mapped_column(Integer)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=text('true'))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    embedding_configuration_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    embeddings: Mapped[Optional["Embeddings"]] = relationship(
        'Embeddings', back_populates='document',uselist=False)
    


class Messages(Base):
    __tablename__ = "messages"
    __table_args__ = (PrimaryKeyConstraint("id", name="messages_pk"),CheckConstraint("role IN ('user', 'ai', 'support_agent', 'system')", name="messages_role_valid"),Index(
            "messages_conversation_created_at_idx",
            "conversation_id",
            "created_at",
        ))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text("now()"))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),server_default=text("now()"),
        onupdate=text('now()'),
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, nullable=True)
    content_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'text'")
    )
    content: Mapped[Optional[str]] = mapped_column(String)
    embedding_configuration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        nullable=True,
    )
    bot_configuration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        nullable=True,
    )
    message_feedback = relationship(
        "MessageFeedback",
        back_populates="message",
    )
        


class TrainingJobs(Base):
    __tablename__ = "training_jobs"
    __table_args__ = (PrimaryKeyConstraint("id", name="training_jobs_pk"),Index(
            "training_jobs_bot_status_idx",
            "bot_id",
            "status",
        ),
        Index(
            "training_jobs_embedding_configuration_id_idx",
            "embedding_configuration_id",
        ))

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[str] = mapped_column(Text, nullable=False)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    # queued, processing, completed, failed, cleanup_completed
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[Optional[datetime.datetime]
                       ] = mapped_column(DateTime(True))
    completed_at: Mapped[Optional[datetime.datetime]
                         ] = mapped_column(DateTime(True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    embedding_configuration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
    bot_configuration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )

class Embeddings(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        ForeignKeyConstraint(["document_id"], ["documents.id"],
                             ondelete="CASCADE", name="embeddings_document_id_fkey"),
        PrimaryKeyConstraint("id", name="embeddings_pkey"),
        Index("embeddings_document_id_idx", "document_id")
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False,unique=True)
    embedding: Mapped[list[float]] = mapped_column(
        VECTOR(1536), nullable=False)   
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()"))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    document: Mapped["Documents"] = relationship(
        "Documents", back_populates="embeddings")


class RetrievalLogs(Base):
    __tablename__ = 'retrieval_logs'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='retrieval_logs_pkey'),Index(
            "retrieval_logs_bot_created_at_idx",
            "bot_id",
            "created_at",
        ),
        Index(
            "retrieval_logs_configuration_id_idx",
            "embedding_configuration_id","llm_configuration_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[Optional[str]] = mapped_column(
        Text,
    )
    bot_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    query: Mapped[Optional[str]] = mapped_column(Text)
    query_embedding: Mapped[Optional[list[float]]
                            ] = mapped_column(VECTOR(1536))
    retrieved_document_ids: Mapped[Optional[list[uuid.UUID]]] = mapped_column(
        ARRAY(Uuid)
    )
    similarity_scores: Mapped[Optional[list[float]]] = mapped_column(
        ARRAY(Float(precision=53))
    )
    retrieval_threshold: Mapped[Optional[float]] = mapped_column(Float(53))
    retrieval_k: Mapped[Optional[int]] = mapped_column(Integer)
    reranker_used: Mapped[Optional[bool]] = mapped_column(Boolean)
    embedding_configuration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        nullable=True,
    )
    llm_configuration_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        nullable=True,
    )
    reranked_document_ids: Mapped[Optional[list[uuid.UUID]]] = mapped_column(
        ARRAY(Uuid))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text('now()'))


class MessageFeedback(Base):
    __tablename__ = 'message_feedback'
    __table_args__ = (
        CheckConstraint(
            "feedback = ANY (ARRAY['positive'::text, 'negative'::text])", name='message_feedback_feedback_check'),
        ForeignKeyConstraint(['message_id'], ['messages.id'],
                             name='message_feedback_message_id_fkey'),
        PrimaryKeyConstraint('id', name='message_feedback_pkey')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    feedback: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text('now()'))

    message: Mapped[Optional['Messages']] = relationship(
        'Messages', back_populates='message_feedback')
    

class EmbeddingConfigurations(Base):
    __tablename__ = 'embedding_configurations'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='embedding_config_pkey'),
        CheckConstraint('chunk_size > 0',name='embedding_config_chunk_size_positive'),
        CheckConstraint('chunk_overlap >= 0 and chunk_overlap < chunk_size',name='embedding_config_chunk_overlap_range'),
        CheckConstraint(
            "dimension > 0",
            name="embedding_config_dimension_positive",
        ),
        CheckConstraint(
            "state IN ('draft', 'training', 'active', 'failed', 'deprecated')",
            name="embedding_config_state_valid",
        ),
        Index(
            'embedding_configurations_one_active_per_bot_idx',
            'bot_id',
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid,nullable=False)
    provider: Mapped[str] = mapped_column(Text,nullable=False)
    model: Mapped[str] = mapped_column(Text,nullable=False)
    version: Mapped[Optional[str]] = mapped_column(Text,nullable=True)
    dimension: Mapped[int] = mapped_column(Integer,nullable=False,server_default=text('1536'))
    chunk_size: Mapped[int] = mapped_column(Integer,nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer,nullable=False)
    state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True),server_default=text('now()'))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True),server_default=text('now()'),onupdate=text('now()'))
    
    
# this is called bot configuration but mostly contains llm and reranker configurations
class BotConfigurations(Base):
    __tablename__ = 'bot_configurations'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='bot_config_pkey'),
        CheckConstraint('retrieval_k > 0',name='bot_config_retrieval_k_positive'),
        CheckConstraint('similarity_threshold >= 0.0 and similarity_threshold <= 1.0',name='bot_config_similarity_threshold_range'),
        CheckConstraint(
            "state IN ('draft', 'training', 'active', 'failed', 'deprecated')",
            name="bot_config_state_valid",
        ),
        Index(
            'bot_configurations_one_active_per_bot_idx',
            'bot_id',
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    bot_id: Mapped[uuid.UUID] = mapped_column(Uuid,nullable=False)
    embedding_configuration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text,nullable=False)
    model: Mapped[str] = mapped_column(Text,nullable=False)
    version: Mapped[Optional[str]] = mapped_column(Text,nullable=True)
    settings: Mapped[Optional[dict]] = mapped_column(JSONB,nullable=True)
    state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
    )
    retrieval_k: Mapped[int] = mapped_column(Integer,nullable=False)
    similarity_threshold: Mapped[float] = mapped_column(Double(53),nullable=False)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid,
        nullable=True,
    )
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True),server_default=text('now()'))
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True),server_default=text('now()'),onupdate=text('now()'))
    
        
        
