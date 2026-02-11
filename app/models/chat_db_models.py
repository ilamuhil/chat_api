from __future__ import annotations

import datetime
import uuid
from typing import Optional
from uuid import UUID

from pgvector.sqlalchemy.vector import VECTOR
from sqlalchemy import (ARRAY, Boolean, CheckConstraint, DateTime, Double,
                        Float, ForeignKeyConstraint, Index, Integer,
                        PrimaryKeyConstraint, String, Text, UniqueConstraint,
                        Uuid, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#Chat database maintained by the python chat server.




class Base(DeclarativeBase):
    pass


class Documents(Base):
    __tablename__ = "documents"
    __table_args__ = (PrimaryKeyConstraint("id", name="documents_pk"),UniqueConstraint(
            "source_id",
            "chunk_index",
            "embedding_model",
            "embedding_version",
            name="uq_documents_source_chunk_model_version",
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
    chunk_size: Mapped[Optional[int]] = mapped_column(Integer)
    chunk_overlap: Mapped[Optional[int]] = mapped_column(Integer)
    section_title: Mapped[Optional[str]] = mapped_column(Text)
    token_count: Mapped[Optional[int]] = mapped_column(Integer)
    embedding_model: Mapped[Optional[str]] = mapped_column(Text)
    embedding_version: Mapped[Optional[str]] = mapped_column(Text)
    embedding_provider: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=text('true'))
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    embeddings: Mapped[list['Embeddings']] = relationship(
        'Embeddings', back_populates='document')


class Messages(Base):
    __tablename__ = "messages"
    __table_args__ = (PrimaryKeyConstraint("id", name="messages_pk"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(String)
    message_feedback: Mapped[list['MessageFeedback']] = relationship(
        'MessageFeedback', back_populates='message')


class TrainingJobs(Base):
    __tablename__ = "training_jobs"
    __table_args__ = (PrimaryKeyConstraint("id", name="training_jobs_pk"),)

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


class Embeddings(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        ForeignKeyConstraint(["document_id"], ["documents.id"],
                             ondelete="CASCADE", name="embeddings_document_id_fkey"),
        PrimaryKeyConstraint("id", name="embeddings_pkey"),
        Index("embeddings_document_id_idx", "document_id"), 
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
        PrimaryKeyConstraint('id', name='retrieval_logs_pkey'),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    bot_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    query: Mapped[Optional[str]] = mapped_column(Text)
    query_embedding: Mapped[Optional[list[float]]
                            ] = mapped_column(VECTOR(1536))
    retrieved_document_ids: Mapped[Optional[list[uuid.UUID]]] = mapped_column(
        ARRAY[UUID](Uuid[UUID]()))
    similarity_scores: Mapped[Optional[list[float]]
                              ] = mapped_column(ARRAY(Float(precision=53)))
    retrieval_threshold: Mapped[Optional[float]] = mapped_column(Float(53))
    retrieval_k: Mapped[Optional[int]] = mapped_column(Integer)
    reranker_used: Mapped[Optional[bool]] = mapped_column(Boolean)
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


class ModelConfigVersions(Base):
    __tablename__ = 'model_config_versions'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='model_config_versions_pkey'),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    bot_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)
    embedding_model: Mapped[Optional[str]] = mapped_column(Text)
    llm_model: Mapped[Optional[str]] = mapped_column(Text)
    reranker_model: Mapped[Optional[str]] = mapped_column(Text)
    retrieval_k: Mapped[Optional[int]] = mapped_column(Integer)
    similarity_threshold: Mapped[Optional[float]] = mapped_column(Double(53))
    chunk_size: Mapped[Optional[int]] = mapped_column(Integer)
    chunk_overlap: Mapped[Optional[int]] = mapped_column(Integer)
    active: Mapped[Optional[bool]] = mapped_column(
        Boolean, server_default=text('false'))
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=text('now()'))
