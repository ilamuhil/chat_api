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


def create_embeddings(
    chat_session: Session,
    documents: list[Documents],
    source_id: str,
    model: str,
    dimensions: int,
) -> None:
  try:
    #Guard against existing embeddings to prevent duplication and empty documents
    existing = chat_session.scalars(
    select(Embeddings.document_id)
    .where(Embeddings.document_id.in_([d.id for d in documents]),Embeddings.deleted_at.is_(None))
    ).all()

    existing_ids = set[UUID](existing)
    documents = [d for d in documents if d.id not in existing_ids]

    if not documents:
        logger.info(f"No new documents to embed for source: {source_id}")
        return

    embeddings = OpenAIEmbeddings(model=model, dimensions=dimensions)
    vectors = embeddings.embed_documents([cast(str, d.content) for d in documents])
    for i, vector in enumerate[list[float]](vectors):
        chat_session.add(
            Embeddings(
                document_id=documents[i].id,
                embedding=vector,
            )
        )
        documents[i].is_active = True
    chat_session.commit()
    logger.info(f"Embeddings created for source: {source_id}")
  except Exception:
    logger.exception(
      "Failed to create embeddings",
      extra={"source_id": str(source_id)},
    )
    raise ValueError("Failed to create embeddings. Please retry.")
  
def retrieve_closest_embeddings(
    chat_session: Session,
    query: list[float],
    bot_id: UUID,
    embedding_configuration_id: UUID,
    k: int = 5,
    threshold: float = 0.5,
) -> list[tuple[Embeddings, Documents, float]]:
  """Return (embedding, document, cosine_distance) rows for the closest matches.

  ``threshold`` is a minimum cosine similarity in ``[0, 1]``. Rows are kept
  when ``1 - cosine_distance >= threshold``.
  """
  try:
    distance = Embeddings.embedding.cosine_distance(query)
    max_distance = max(0.0, min(1.0, 1.0 - threshold))
    stmnt = (
      select(Embeddings, Documents, distance)
      .join(Documents, Embeddings.document_id == Documents.id)
      .where(
        Documents.bot_id == bot_id,
        Documents.is_active.is_(True),
        Documents.deleted_at.is_(None),
        Documents.embedding_configuration_id == embedding_configuration_id,
        distance <= max_distance,
      )
      .order_by(distance, Documents.chunk_index)
      .limit(k)
    )
    rows = chat_session.execute(stmnt).all()
    return [
      (embedding, document, float(dist))
      for embedding, document, dist in rows
    ]
  except Exception as e:
    logger.exception("Failed to retrieve closest embeddings",extra={"error": str(e)})
    raise ValueError("Failed to retrieve closest embeddings. Please retry.")


def embed_query(query: str, model: str, dimensions: int) -> list[float]:
  try:
    embeddings = OpenAIEmbeddings(
            model=model,
            dimensions=dimensions,
        )
    query_vector = embeddings.embed_query(query)
    return query_vector
  except Exception as e:
    logger.exception("Failed to embed query",extra={"error": str(e)})  
    raise ValueError("Failed to embed query. Please retry.")

def count_tokens(text: str, model: str) -> int:
    enc = _ENCODINGS.get(model)
    if enc is None:
        enc = tiktoken.encoding_for_model(model)
        _ENCODINGS[model] = enc
    return len(enc.encode(text))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))  