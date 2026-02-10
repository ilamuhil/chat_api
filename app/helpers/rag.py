import logging
from typing import cast
from uuid import UUID

import numpy as np
import tiktoken
from langchain_openai import OpenAIEmbeddings
from pgvector.sqlalchemy import VECTOR
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.rag_config import _EMBEDDING_CONFIG
from app.models.chat_db_models import Documents, Embeddings

logger = logging.getLogger(__name__)
_ENCODINGS: dict[str, tiktoken.Encoding] = {}


def create_embeddings(chat_session: Session, documents: list[Documents],source_id: str) -> None:
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

    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small", dimensions=1536,
    )
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
  
def retrieve_closest_embeddings(chat_session: Session, query:list[float], bot_id: UUID, k: int=5, threshold:float = 0.5,CURRENT_MODEL: str=_EMBEDDING_CONFIG["model"], CURRENT_VERSION: str=_EMBEDDING_CONFIG["version"]):
  try: 
    distance = Embeddings.embedding.cosine_distance(query)
    stmnt =  select(Embeddings,Documents).join(Documents, Embeddings.document_id == Documents.id).where(
        Documents.bot_id == bot_id,
        Documents.is_active.is_(True),
        Documents.deleted_at.is_(None),
        Documents.embedding_model == CURRENT_MODEL,
        Documents.embedding_version == CURRENT_VERSION,
        distance <= threshold
      ).order_by(distance,Documents.chunk_index).limit(k)
    similar_embeddings_with_documents = chat_session.execute(stmnt).all()
    return similar_embeddings_with_documents
  except Exception as e:
    logger.exception("Failed to retrieve closest embeddings",extra={"error": str(e)})  
    raise ValueError("Failed to retrieve closest embeddings. Please retry.")


def embed_query(query: str, CURRENT_MODEL: str=_EMBEDDING_CONFIG["model"]):
  try:
    embeddings = OpenAIEmbeddings(
    model=CURRENT_MODEL,dimensions=_EMBEDDING_CONFIG["dimensions"])
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