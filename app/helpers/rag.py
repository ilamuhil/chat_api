import logging
from typing import cast
from uuid import UUID

import numpy as np
import tiktoken
from langchain_openai import OpenAIEmbeddings
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.chat_db_models import Documents, Embeddings

logger = logging.getLogger(__name__)
_ENCODINGS: dict[str, tiktoken.Encoding] = {}


def create_embeddings(py_session: Session, documents: list[Documents],source_id: str) -> None:
  try:
    #Guard against existing embeddings to prevent duplication and empty documents
    existing = py_session.scalars(
    select(Embeddings.document_id)
    .where(Embeddings.document_id.in_([d.id for d in documents]))
    ).all()

    existing_ids = set[UUID](existing)
    documents = [d for d in documents if d.id not in existing_ids]

    if not documents:
        logger.info(f"No new documents to embed for source: {source_id}")
        return

    embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",dimensions=1536)
    with py_session.begin():
      vectors = embeddings.embed_documents([cast(str, document.content) for document in documents])
      for i, vector in enumerate[list[float]](vectors):
        py_session.add(Embeddings(
          document_id=documents[i].id,
          embedding=vector,
        ))
        documents[i].is_active = True
    logger.info(f"Embeddings created for source: {source_id}")
  except Exception:
    logger.exception(
      "Failed to create embeddings",
      extra={"source_id": str(source_id)},
    )
    raise ValueError("Failed to create embeddings. Please retry.")
  
  


def count_tokens(text: str, model: str) -> int:
    enc = _ENCODINGS.get(model)
    if enc is None:
        enc = tiktoken.encoding_for_model(model)
        _ENCODINGS[model] = enc
    return len(enc.encode(text))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))  