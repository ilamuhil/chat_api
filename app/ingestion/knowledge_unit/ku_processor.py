from app.rag.embeddings import count_tokens

from .knowledge_unit import ChunkingConfig, KnowledgeUnit
from .ku_merger import KnowledgeUnitMerger
from .ku_rules import KnowledgeUnitCleaner
from .ku_splitter import KnowledgeUnitSplitter


class KnowledgeUnitProcessor:
    """Clean -> split oversized prose -> merge weak units -> finalize."""

    def __init__(self, embedding_model: str, chunking_config: ChunkingConfig):
        self.embedding_model = embedding_model
        self.config = chunking_config
        self.cleaner = KnowledgeUnitCleaner()

    def process(self, knowledge_units: list[KnowledgeUnit]) -> list[KnowledgeUnit]:
        if not knowledge_units:
            return []
        if len({unit.source_type for unit in knowledge_units}) != 1:
            raise ValueError("All knowledge units must have the same source type")

        # Local to one run: no stale metadata counts or long-lived text cache.
        cache: dict[str, int] = {}

        def tokens(text: str) -> int:
            if text not in cache:
                cache[text] = count_tokens(text, self.embedding_model)
            return cache[text]

        splitter = KnowledgeUnitSplitter(self.config, tokens)
        merger = KnowledgeUnitMerger(self.config, tokens)
        units: list[KnowledgeUnit] = []
        for original in knowledge_units:
            cleaned = self.cleaner.clean(original)
            if cleaned is not None:
                units.extend(splitter.split(cleaned))

        result = merger.merge(units)
        for index, unit in enumerate(result):
            size = tokens(unit.content)
            if unit.is_splittable_prose and size > self.config.max_tokens:
                raise RuntimeError(f"Prose unit {index} exceeds max_tokens")
            unit.source_order = index
            unit.metadata["token_count"] = size
        return result
