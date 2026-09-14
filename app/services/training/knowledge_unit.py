from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Self


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


@dataclass(slots=True)
class KnowledgeUnit:
    source_type: SourceType
    content: str = field(default="")
    metadata: dict[str, Any] = field(default_factory=dict)
    source_order: int = field(default=0)

    @property
    def page(self) -> int | None:
        return self.metadata.get("source", {}).get("page", None)

    @property
    def content_type(self) -> str | None:
        return self.metadata.get("structure", {}).get("content_type", None)

    @property
    def heading_path(self) -> list[str]:
        return self.metadata.get("structure", {}).get("heading_path", [])

    @property
    def section_title(self) -> str | None:
        path = self.heading_path
        return path[-1] if path else None

    def pretty_print(self) -> None:
        print(f"Content: {self.content}")
        print(f"Metadata: {self.metadata}")
        print(f"Source Order: {self.source_order}")
        print(f"Page: {self.page}")
        print(f"Content Type: {self.content_type}")
        print(f"Heading Path: {self.heading_path}")
        print(f"Section Title: {self.section_title}")
        print("-" * 100)

    @classmethod
    def consolidate(cls, knowledge_units: list[Self]) -> list[Self]:
        source_types = set(map(lambda x: x.source_type, knowledge_units))
        if len(source_types) != 1:
            raise ValueError("All knowledge units must have the same source type")
        source_type = source_types.pop()

        MIN_TOKENS, TARGET_TOKENS, MAX_TOKENS = 30, 250, 400

        if source_type == SourceType.HTML:
            pass

        return []
