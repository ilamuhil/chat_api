from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceType(StrEnum):
    HTML = "html"
    MARKDOWN = "markdown"
    PDF = "pdf"
    CSV = "csv"
    XLSX = "xlsx"
    DOCX = "docx"
    TXT = "txt"


class ContentType(StrEnum):
    TEXT = "text"
    TABLE = "table"
    LIST = "list"
    CODE = "code"
    OTHER = "other"


class WeakReason(StrEnum):
    SHORT = "short"
    BROKEN_SENTENCE = "broken_sentence"
    CONTEXT_DEPENDENT = "context_dependent"
    ORPHAN_FAQ = "orphan_faq"
    HEADING_ONLY = "heading_only"
    VALUE_ONLY = "value_only"


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    # Retain the original positional order for compatibility.
    min_tokens: int
    max_tokens: int
    target_tokens: int

    def __post_init__(self) -> None:
        values = (self.min_tokens, self.target_tokens, self.max_tokens)
        if any(type(value) is not int for value in values):
            raise TypeError("Chunk token limits must be integers")
        if not 0 < self.min_tokens <= self.target_tokens <= self.max_tokens:
            raise ValueError("Expected 0 < min_tokens <= target_tokens <= max_tokens")


@dataclass(slots=True)
class KnowledgeUnit:
    source_type: SourceType
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    source_order: int = 0

    @property
    def page(self) -> int | None:
        return self.metadata.get("source", {}).get("page")

    @property
    def content_type(self) -> str | None:
        return self.metadata.get("structure", {}).get("content_type")

    @property
    def heading_paths(self) -> list[str]:
        return self.metadata.get("structure", {}).get("heading_paths", [])

    @property
    def is_splittable_prose(self) -> bool:
        structure = self.metadata.get("structure", {})
        return (
            self.content_type in {"paragraph", "text"}
            and "question" not in structure
            and "answer" not in structure
        )

    @classmethod
    def consolidate(
        cls,
        knowledge_units: list[KnowledgeUnit],
        embedding_model: str,
        chunking_config: ChunkingConfig,
    ) -> list[KnowledgeUnit]:
        # Local import avoids a circular dependency. Existing callers still work.
        from .ku_processor import KnowledgeUnitProcessor

        return KnowledgeUnitProcessor(embedding_model, chunking_config).process(
            knowledge_units
        )
