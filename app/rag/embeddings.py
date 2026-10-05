import logging
from typing import cast
from uuid import UUID

import tiktoken
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chat_db_models import Documents, Embeddings

logger = logging.getLogger(__name__)
_ENCODINGS: dict[str, tiktoken.Encoding] = {}


def create_embeddings(
    chat_session: Session,
    documents: list[Documents],
    source_id: str,
    model: str,
    dimensions: int,
) -> None:
    """Stage vectors and activation in the caller's source transaction."""
    try:
        # Guard against existing embeddings to prevent duplication and empty documents
        existing = chat_session.scalars(
            select(Embeddings.document_id).where(
                Embeddings.document_id.in_([d.id for d in documents]),
                Embeddings.deleted_at.is_(None),
            )
        ).all()

        existing_ids = set[UUID](existing)
        documents = [d for d in documents if d.id not in existing_ids]

        if not documents:
            logger.info(f"No new documents to embed for source: {source_id}")
            return

        embeddings = OpenAIEmbeddings(model=model, dimensions=dimensions)
        vectors = embeddings.embed_documents([cast(str, d.content) for d in documents])
        if len(vectors) != len(documents) or any(
            len(vector) != dimensions for vector in vectors
        ):
            raise ValueError(
                "Embedding response does not match the requested documents/dimensions"
            )
        for document, vector in zip(documents, vectors, strict=True):
            chat_session.add(
                Embeddings(
                    document_id=document.id,
                    embedding=vector,
                )
            )
            document.is_active = True
        chat_session.flush()
        logger.info(f"Embeddings created for source: {source_id}")
    except Exception as error:
        logger.exception(
            "Failed to create embeddings",
            extra={"source_id": str(source_id)},
        )
        raise ValueError("Failed to create embeddings. Please retry.") from error


def count_tokens(text: str, model: str) -> int:
    enc = _ENCODINGS.get(model)
    if enc is None:
        enc = tiktoken.encoding_for_model(model)
        _ENCODINGS[model] = enc
    return len(enc.encode(text, disallowed_special=()))


def get_tokenizer(model: str) -> tiktoken.Encoding:
    enc = _ENCODINGS.get(model)
    if enc is None:
        enc = tiktoken.encoding_for_model(model)
        _ENCODINGS[model] = enc
    return enc
