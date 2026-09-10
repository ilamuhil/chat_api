from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class KnowledgeUnit:
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
