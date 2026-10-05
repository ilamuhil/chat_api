from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, NotRequired, TypedDict


class SourceType(StrEnum):
    HTML = "html"
    MARKDOWN = "markdown"
    PDF = "pdf"
    CSV = "csv"
    XLSX = "xlsx"
    DOCX = "docx"
    TXT = "txt"


class ContentType(StrEnum):
    """Canonical semantic type for a knowledge-unit's content."""

    TEXT = "text"
    HEADING = "heading"
    LIST = "list"
    TABLE = "table"
    MIXED = "mixed"
    OTHER = "other"


class SourceMetadata(TypedDict, total=False):
    """Common source/provenance fields carried by a knowledge unit."""

    filename: NotRequired[str]
    source_id: NotRequired[str | None]
    url: NotRequired[str | None]
    canonical_url: NotRequired[str | None]
    title: NotRequired[str | None]
    description: NotRequired[str | None]
    og_title: NotRequired[str | None]
    og_description: NotRequired[str | None]
    language: NotRequired[str | None]
    page: NotRequired[int | None]
    pages: NotRequired[list[int]]
    provenance: NotRequired[dict[str, list[dict[str, Any]]]]


class StructureMetadata(TypedDict, total=False):
    """Common structural fields produced by document parsers."""

    heading_paths: NotRequired[list[str]]
    content_type: NotRequired[ContentType]
    content_types: NotRequired[list[ContentType]]
    labels: NotRequired[list[str | None]]
    source_items: NotRequired[list[dict[str, Any]]]
    columns: NotRequired[list[str]]
    headers: NotRequired[list[str]]
    rows: NotRequired[list[list[str]]]
    list_kind: NotRequired[Literal["ordered", "unordered", "mixed"]]
    heading_level: NotRequired[int]
    num_rows: NotRequired[int]
    num_columns: NotRequired[int]
    table_info: NotRequired[dict[str, Any]]
    code_language: NotRequired[str | None]
    image_available: NotRequired[bool]
    caption_refs: NotRequired[list[str]]
    footnote_refs: NotRequired[list[str]]
    question: NotRequired[str | None]
    answer: NotRequired[str | None]
    extra: NotRequired[dict[str, Any]]


class SplitMetadata(TypedDict, total=False):
    """Metadata describing how a unit was split or merged."""

    parent_source_order: NotRequired[int]
    index: NotRequired[int]
    count: NotRequired[int]


class KnowledgeUnitMetadata(TypedDict, total=False):
    """Top-level metadata envelope stored with each knowledge unit."""

    source: NotRequired[SourceMetadata]
    structure: NotRequired[StructureMetadata]
    token_count: NotRequired[int]
    split: NotRequired[SplitMetadata]
    split_parts: NotRequired[list[SplitMetadata]]
    extra: NotRequired[dict[str, Any]]


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
    metadata: KnowledgeUnitMetadata = field(default_factory=dict)
    source_order: int = 0

    @property
    def page(self) -> int | None:
        return self.metadata.get("source", {}).get("page")

    @property
    def content_type(self) -> ContentType | None:
        value = self.metadata.get("structure", {}).get("content_type")
        if value is None:
            return None
        if isinstance(value, ContentType):
            return value
        try:
            return ContentType(value)
        except ValueError:
            return ContentType.OTHER

    @property
    def heading_paths(self) -> list[str]:
        return self.metadata.get("structure", {}).get("heading_paths", [])

    @property
    def content_types(self) -> list[ContentType]:
        """Return all semantic types represented by this unit."""
        structure = self.metadata.get("structure", {})
        types = structure.get("content_types")
        if types:
            return [
                value if isinstance(value, ContentType) else ContentType(value)
                for value in types
            ]
        content_type = self.content_type
        return [] if content_type is None else [content_type]

    @property
    def is_splittable_prose(self) -> bool:
        structure = self.metadata.get("structure", {})
        return (
            self.content_type is ContentType.TEXT
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
        from .ku_processor import KnowledgeUnitProcessor

        return KnowledgeUnitProcessor(embedding_model, chunking_config).process(
            knowledge_units
        )
