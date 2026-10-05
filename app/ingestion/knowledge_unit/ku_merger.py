from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace

from .knowledge_unit import (
    ChunkingConfig,
    ContentType,
    KnowledgeUnit,
    KnowledgeUnitMetadata,
    SplitMetadata,
    StructureMetadata,
    WeakReason,
)
from .ku_rules import KnowledgeUnitWeaknessDetector


class KnowledgeUnitMerger:
    """Repair weak adjacent units, preferring the highest-scoring safe merge."""

    def __init__(self, config: ChunkingConfig, tokens: Callable[[str], int]):
        self.config = config
        self.tokens = tokens
        self.detector = KnowledgeUnitWeaknessDetector()

    def merge(self, units: list[KnowledgeUnit]) -> list[KnowledgeUnit]:
        result: list[KnowledgeUnit] = []
        for index, current in enumerate(units):
            reasons = self.detector.detect(
                current, self.tokens(current.content), self.config.min_tokens
            )
            if not reasons:
                result.append(current)
                continue

            previous = result[-1] if result else None
            following = units[index + 1] if index + 1 < len(units) else None
            candidates = [
                (self.score(current, previous, reasons), "previous", previous),
                (self.score(current, following, reasons), "following", following),
            ]
            # Stable sort preserves backward preference on equal scores.
            candidates.sort(key=lambda item: item[0], reverse=True)
            for score, direction, candidate in candidates:
                if candidate is None or score < 0:
                    continue
                left, right = (
                    (candidate, current)
                    if direction == "previous"
                    else (current, candidate)
                )
                content = f"{left.content}\n{right.content}"
                size = self.tokens(content)
                if size > self.config.max_tokens:
                    continue
                metadata = self.merge_metadata(left, right)
                metadata["token_count"] = size
                merged = replace(left, content=content, metadata=metadata)
                if direction == "previous":
                    result[-1] = merged
                else:
                    # The next loop iteration re-evaluates the merged unit.
                    units[index + 1] = merged
                break
            else:
                result.append(current)
        return result

    @staticmethod
    def compatible(
        left: ContentType | None,
        right: ContentType | None,
    ) -> bool:
        if left is None or right is None or left == right:
            return True
        pairs = {
            frozenset({ContentType.TEXT, ContentType.LIST}),
            frozenset({ContentType.TEXT, ContentType.HEADING}),
        }
        return frozenset({left, right}) in pairs

    def score(
        self,
        unit: KnowledgeUnit,
        candidate: KnowledgeUnit | None,
        reasons: set[WeakReason],
    ) -> int:
        if candidate is None or not self.compatible(
            unit.content_type, candidate.content_type
        ):
            return -1
        # Never merge different known sources even if their format matches.
        for key in ("source_id", "filename", "url", "canonical_url"):
            left = unit.metadata.get("source", {}).get(key)
            right = candidate.metadata.get("source", {}).get(key)
            if left is not None and right is not None and left != right:
                return -1
        score = 0
        if unit.page is not None and unit.page == candidate.page:
            score += 3
        elif (
            unit.page is not None
            and candidate.page is not None
            and abs(unit.page - candidate.page) == 1
        ):
            score += 1
        if unit.heading_paths and unit.heading_paths == candidate.heading_paths:
            score += 4
        elif not unit.heading_paths or not candidate.heading_paths:
            score += 1
        else:
            return -1
        score += 2 * len(
            reasons
            & {
                WeakReason.BROKEN_SENTENCE,
                WeakReason.CONTEXT_DEPENDENT,
                WeakReason.VALUE_ONLY,
                WeakReason.HEADING_ONLY,
            }
        )
        return score

    @staticmethod
    def merge_metadata(
        left: KnowledgeUnit,
        right: KnowledgeUnit,
    ) -> KnowledgeUnitMetadata:
        metadata = deepcopy(right.metadata)
        metadata.update(deepcopy(left.metadata))
        ls, rs = left.metadata.get("source", {}), right.metadata.get("source", {})
        source = deepcopy(rs)
        source.update(deepcopy(ls))
        pages = set(ls.get("pages", []) or []) | set(rs.get("pages", []) or [])
        pages.update(p for p in (left.page, right.page) if p is not None)
        if pages:
            source.update(page=min(pages), pages=sorted(pages))
        if "provenance" in ls or "provenance" in rs:
            # Preserve the existing dictionary-based provenance convention.
            source["provenance"] = deepcopy(
                {**ls.get("provenance", {}), **rs.get("provenance", {})}
            )
        lstruct, rstruct = (
            left.metadata.get("structure", {}),
            right.metadata.get("structure", {}),
        )
        structure: StructureMetadata = deepcopy(rstruct)
        structure.update(deepcopy(lstruct))
        structure["heading_paths"] = deepcopy(left.heading_paths or right.heading_paths)
        for key in ("source_items", "labels"):
            if key in lstruct or key in rstruct:
                structure[key] = deepcopy(lstruct.get(key, []) + rstruct.get(key, []))
        left_type = left.content_type
        right_type = right.content_type
        if left_type is not None and left_type == right_type:
            structure["content_type"] = left_type
            if left_type is ContentType.MIXED:
                structure["content_types"] = list(
                    dict.fromkeys(left.content_types + right.content_types)
                )
        elif left_type is None and right_type is None:
            structure["content_type"] = ContentType.OTHER
        elif {left_type, right_type} <= {ContentType.TEXT, None}:
            structure["content_type"] = ContentType.TEXT
        else:
            structure["content_type"] = ContentType.MIXED
            structure["content_types"] = list(
                dict.fromkeys(left.content_types + right.content_types)
            )
        metadata.update(source=source, structure=structure)
        metadata.pop("token_count", None)
        # A single child's split index is misleading after merging children.
        parts: list[SplitMetadata] = []
        for unit in (left, right):
            parts.extend(deepcopy(unit.metadata.get("split_parts", [])))
            if "split" in unit.metadata:
                parts.append(deepcopy(unit.metadata["split"]))
        metadata.pop("split", None)
        metadata.pop("split_parts", None)
        if parts:
            metadata["split_parts"] = parts
        return metadata
