from __future__ import annotations

import datetime
import uuid
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Documents(Base):
    __tablename__ = "documents"
    __table_args__ = (PrimaryKeyConstraint("id", name="documents_pk"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    organization_id: Mapped[str] = mapped_column(Text, nullable=False)
    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text("now()"))

    embeddings: Mapped[list["Embeddings"]] = relationship("Embeddings", back_populates="document")


class Messages(Base):
    __tablename__ = "messages"
    __table_args__ = (PrimaryKeyConstraint("id", name="messages_pk"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[Optional[str]] = mapped_column(String)


class TrainingJobs(Base):
    __tablename__ = "training_jobs"
    __table_args__ = (PrimaryKeyConstraint("id", name="training_jobs_pk"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[str] = mapped_column(Text, nullable=False)
    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))


class Embeddings(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE", name="embeddings_document_id_fkey"),
        PrimaryKeyConstraint("id", name="embeddings_pkey"),
        Index("embeddings_document_id_idx", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("gen_random_uuid()"))
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text("now()"))

    document: Mapped["Documents"] = relationship("Documents", back_populates="embeddings")


