import logging
from uuid import UUID

from sqlalchemy.orm import Session

# import database session
from app.models.chat_db_models import EmbeddingConfigurations
from app.rag.context_assembler import ContextAssembler
from app.rag.hybrid_retriever import HybridRetriever
from app.rag.query_preparer import QueryPreparer
from app.rag.result_ranker import ResultRanker

logger = logging.getLogger(__name__)


class RetrievalPipeline:
    embedding_configuration: EmbeddingConfigurations
    query_preparer: QueryPreparer
    result_ranker: ResultRanker
    context_assembler: ContextAssembler
    hybrid_retriever: HybridRetriever
    chat_session: Session

    def __init__(self, chat_session: Session):
        self.chat_session = chat_session
        self.query_preparer = QueryPreparer()
        self.result_ranker = ResultRanker()
        self.context_assembler = ContextAssembler()
        self.hybrid_retriever = HybridRetriever()

    def get_embedding_configuration(
        self, embedding_configuration_id: UUID
    ) -> EmbeddingConfigurations | None:
        return (
            self.chat_session.query(EmbeddingConfigurations)
            .filter(EmbeddingConfigurations.id == embedding_configuration_id)
            .first()
        )
