from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import TypeVar

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .knowledge_unit import ChunkingConfig, KnowledgeUnit

Unit = TypeVar("Unit", bound=KnowledgeUnit)


class KnowledgeUnitSplitter:
    """Split only oversized prose; never split structured units here."""

    def __init__(self, config: ChunkingConfig, tokens: Callable[[str], int]):
        self.config = config
        self.tokens = tokens
        self._splitter: RecursiveCharacterTextSplitter | None = None

    def split(self, unit: Unit) -> list[Unit]:
        if (
            not unit.is_splittable_prose
            or self.tokens(unit.content) <= self.config.max_tokens
        ):
            return [unit]

        # Construct once, and only if this processing call needs splitting.
        if self._splitter is None:
            self._splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.config.target_tokens,
                chunk_overlap=0,
                length_function=self.tokens,
                separators=[r"\n[ \t]*\n", r"\n", r"(?<=[.!?。！？])\s+", r"\s+", ""],
                is_separator_regex=True,
                keep_separator=True,
                strip_whitespace=False,
            )

        pending = list(reversed(self._splitter.split_text(unit.content)))
        chunks: list[tuple[str, int]] = []
        while pending:
            text = pending.pop()
            size = self.tokens(text)
            if size <= self.config.max_tokens:
                chunks.append((text, size))
                continue
            if len(text) <= 1:
                raise ValueError("max_tokens cannot hold a single character")
            middle = len(text) // 2
            pending.extend([text[middle:], text[:middle]])

        if "".join(text for text, _ in chunks) != unit.content:
            raise RuntimeError("Splitting changed or dropped source content")

        children: list[Unit] = []
        for index, (text, size) in enumerate(chunks):
            metadata = deepcopy(unit.metadata)
            metadata["token_count"] = size
            metadata["split"] = {
                "parent_source_order": unit.source_order,
                "index": index,
                "count": len(chunks),
            }
            children.append(replace(unit, content=text, metadata=metadata))
        return children
