from dataclasses import dataclass, field
from typing import Any


@dataclass()
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
    def heading_path(self) -> list[str] | None:
        return self.metadata.get("structure", {}).get("heading_path", [])

    @property
    def section_title(self) -> str | None:
        path = self.heading_path
        return path[-1] if path else None


@dataclass()
class Extractor:
    html: str = field(default="")
    heading_state: dict[str, Any] = field(default_factory=dict)
    source_order: int = field(default=0)

    def current_heading_path(self) -> list[str] | None:
        return
