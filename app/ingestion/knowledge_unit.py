import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Self

from app.helpers.rag import count_tokens


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
    min_tokens: int
    max_tokens: int
    target_tokens: int


@dataclass(slots=True)
class KnowledgeUnit:
    source_type: SourceType
    content: str = field(default="")
    metadata: dict[str, Any] = field(default_factory=dict)
    source_order: int = field(default=0)

    JUNK_EXACT_TEXT: ClassVar[frozenset[str]] = frozenset(
        {
            "",
            "+",
            "-",
            "_",
            "|",
            "/",
            "\\",
            ".",
            "..",
            "...",
            "•",
            "→",
            "←",
            "↑",
            "↓",
            "›",
            "»",
            "«",
            "✓",
            "✔",
            "✗",
            "✘",
            "★",
            "☆",
        }
    )

    UI_JUNK_TEXT: ClassVar[frozenset[str]] = frozenset(
        {
            "menu",
            "close",
            "read more",
            "share",
            "like",
            "comment",
            "follow",
            "subscribe",
            "powered by",
            "facebook",
            "instagram",
            "twitter",
        }
    )

    CONTEXT_PREFIXES: ClassVar[tuple[str, ...]] = (
        "this ",
        "that ",
        "these ",
        "those ",
        "it ",
        "they ",
        "the above",
        "the following",
        "such ",
    )

    BROKEN_START_WORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "and",
            "or",
            "but",
            "which",
            "that",
            "because",
            "including",
            "depending",
            "while",
            "whereas",
        }
    )

    BROKEN_END_WORDS: ClassVar[frozenset[str]] = frozenset(
        {
            "and",
            "or",
            "with",
            "for",
            "of",
            "to",
            "including",
            "because",
            "from",
        }
    )

    URL_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"https?://(?:www\.)?"
        r"[-a-zA-Z0-9@:%._+~#=]{1,256}"
        r"\.[a-zA-Z0-9()]{2,63}\b"
        r"(?:[-a-zA-Z0-9()@:%_+.~#?&/=]*)",
        flags=re.IGNORECASE,
    )

    CONTROL_CHAR_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F-\u009F]"
    )

    @property
    def page(self) -> int | None:
        return self.metadata.get("source", {}).get("page")

    @property
    def content_type(self) -> str | None:
        return self.metadata.get("structure", {}).get("content_type")

    @property
    def heading_paths(self) -> list[str]:
        return self.metadata.get("structure", {}).get("heading_paths", [])

    def _clean_content(self) -> str:
        content = self.CONTROL_CHAR_PATTERN.sub("", self.content)

        # Intentional:
        # URLs are represented separately as bot resources.
        content = self.URL_PATTERN.sub("", content)

        content = re.sub(r"[ \t]+", " ", content)
        content = re.sub(r" +\n", "\n", content)
        content = re.sub(r"\n{3,}", "\n\n", content)

        return content.strip()

    def _is_junk(self, content: str) -> bool:
        normalized = content.strip().casefold()

        if not normalized:
            return True

        if normalized in self.JUNK_EXACT_TEXT:
            return True

        if normalized in self.UI_JUNK_TEXT:
            return True

        if not any(char.isalnum() for char in content):
            return True

        if self._is_symbol_heavy(content):
            return True

        if self._is_repeated_character_noise(content):
            return True

        return False

    @staticmethod
    def _is_symbol_heavy(content: str) -> bool:
        alphanumeric = 0
        noise = 0

        for char in content:
            if char.isspace():
                continue

            if char.isalnum():
                alphanumeric += 1
                continue

            category = unicodedata.category(char)

            if category.startswith(("P", "S")):
                noise += 1

        total = alphanumeric + noise

        if total == 0:
            return True

        return alphanumeric <= 3 and (noise / total) >= 0.70

    @staticmethod
    def _is_repeated_character_noise(content: str) -> bool:
        compact = re.sub(r"\s+", "", content)

        if len(compact) < 3:
            return False

        return bool(
            re.fullmatch(
                r"([^A-Za-z0-9])\1{2,}",
                compact,
            )
        )

    def weak_reasons(
        self,
        embedding_model: str,
        min_tokens: int,
    ) -> set[WeakReason]:
        reasons: set[WeakReason] = set()

        content = self.content.strip()
        token_count = count_tokens(content, embedding_model)

        # --------------------------------------------------
        # 1. Short
        # --------------------------------------------------

        if token_count < min_tokens:
            reasons.add(WeakReason.SHORT)

        # --------------------------------------------------
        # 2. Heading-only
        # --------------------------------------------------

        if self.content_type == "heading":
            reasons.add(WeakReason.HEADING_ONLY)

        # --------------------------------------------------
        # 3. Broken / incomplete prose
        # --------------------------------------------------

        if self._looks_like_broken_sentence(content):
            reasons.add(WeakReason.BROKEN_SENTENCE)

        # --------------------------------------------------
        # 4. Context-dependent text
        # --------------------------------------------------

        if self._looks_context_dependent(content):
            reasons.add(WeakReason.CONTEXT_DEPENDENT)

        # --------------------------------------------------
        # 5. FAQ with missing side
        # --------------------------------------------------

        if self._is_orphan_faq():
            reasons.add(WeakReason.ORPHAN_FAQ)

        # --------------------------------------------------
        # 6. Tiny value-only fragment
        # --------------------------------------------------

        if token_count < min_tokens and self._looks_like_value_only(content):
            reasons.add(WeakReason.VALUE_ONLY)

        return reasons

    @classmethod
    def _looks_like_broken_sentence(
        cls,
        content: str,
    ) -> bool:
        words = re.findall(r"\b[\w'-]+\b", content.casefold())

        if not words:
            return False

        if words[0] in cls.BROKEN_START_WORDS:
            return True

        if words[-1] in cls.BROKEN_END_WORDS:
            return True

        return False

    @classmethod
    def _looks_context_dependent(
        cls,
        content: str,
    ) -> bool:
        normalized = content.casefold().strip()

        return normalized.startswith(cls.CONTEXT_PREFIXES)

    def _is_orphan_faq(self) -> bool:
        structure = self.metadata.get("structure", {})

        question = structure.get("question")
        answer = structure.get("answer")

        # Only treat it as FAQ if FAQ metadata actually exists.
        if question is None and answer is None:
            return False

        return not bool(question) or not bool(answer)

    @staticmethod
    def _looks_like_value_only(content: str) -> bool:
        stripped = content.strip()

        # Currency / numeric value.
        if re.fullmatch(
            r"[₹$€£]?\s*\d[\d,]*(?:\.\d+)?(?:\s*[%₹$€£])?",
            stripped,
        ):
            return True

        # Time / duration-like values.
        if re.fullmatch(
            r"\d+(?:\.\d+)?\s*"
            r"(day|days|week|weeks|month|months|year|years|hour|hours)",
            stripped,
            flags=re.IGNORECASE,
        ):
            return True

        return False

    @classmethod
    def consolidate(
        cls,
        knowledge_units: list[Self],
        embedding_model: str,
        chunking_config: ChunkingConfig,
    ) -> list[Self]:
        if not knowledge_units:
            return []

        source_types = {unit.source_type for unit in knowledge_units}

        if len(source_types) != 1:
            raise ValueError("All knowledge units must have the same source type")

        # --------------------------------------------------
        # Phase 1: clean + remove hard junk
        # --------------------------------------------------

        units: list[Self] = []

        for unit in knowledge_units:
            content = unit._clean_content()

            if unit._is_junk(content):
                continue

            unit.content = content
            units.append(unit)

        if not units:
            return []

        # --------------------------------------------------
        # Phase 2: weak-unit consolidation
        # --------------------------------------------------

        consolidated: list[Self] = []
        i = 0

        while i < len(units):
            current = units[i]

            reasons = current.weak_reasons(
                embedding_model,
                chunking_config.min_tokens,
            )

            # No weakness detected.
            if not reasons:
                consolidated.append(current)
                i += 1
                continue

            previous = consolidated[-1] if consolidated else None

            next_unit = units[i + 1] if i + 1 < len(units) else None

            previous_score = cls._merge_score(
                current,
                previous,
                reasons,
            )

            next_score = cls._merge_score(
                current,
                next_unit,
                reasons,
            )

            # Prefer backward merge on equal score because
            # content normally belongs to preceding context.
            if (
                previous is not None
                and previous_score >= next_score
                and previous_score >= 0
            ):
                content = f"{previous.content}\n{current.content}"

                if count_tokens(content, embedding_model) <= chunking_config.max_tokens:
                    previous.content = content
                    previous.metadata = cls._merge_metadata(
                        previous,
                        current,
                    )

                    i += 1
                    continue

            if next_unit is not None and next_score >= 0:
                content = f"{current.content}\n{next_unit.content}"

                if count_tokens(content, embedding_model) <= chunking_config.max_tokens:
                    next_unit.content = content
                    next_unit.metadata = cls._merge_metadata(
                        current,
                        next_unit,
                    )

                    i += 1
                    continue

            # Weak is not the same as junk.
            # If there is no safe merge, preserve it.
            consolidated.append(current)
            i += 1

        for source_order, unit in enumerate(consolidated):
            unit.source_order = source_order

        return consolidated

    @classmethod
    def _merge_score(
        cls,
        unit: Self,
        candidate: Self | None,
        reasons: set[WeakReason],
    ) -> int:
        if candidate is None:
            return -1

        if not cls._compatible_content_types(
            unit.content_type,
            candidate.content_type,
        ):
            return -1

        score = 0

        # Same page.
        if unit.page is not None and unit.page == candidate.page:
            score += 3

        # Neighboring page.
        elif (
            unit.page is not None
            and candidate.page is not None
            and abs(unit.page - candidate.page) == 1
        ):
            score += 1

        # Same structural heading.
        if unit.heading_paths and unit.heading_paths == candidate.heading_paths:
            score += 4

        elif not unit.heading_paths or not candidate.heading_paths:
            score += 1

        else:
            # Explicitly different sections.
            return -1

        # Certain weaknesses have stronger need for context.
        if WeakReason.BROKEN_SENTENCE in reasons:
            score += 2

        if WeakReason.CONTEXT_DEPENDENT in reasons:
            score += 2

        if WeakReason.VALUE_ONLY in reasons:
            score += 2

        if WeakReason.HEADING_ONLY in reasons:
            score += 2

        return score

    @staticmethod
    def _compatible_content_types(
        left: str | None,
        right: str | None,
    ) -> bool:
        if left is None or right is None:
            return True

        if left == right:
            return True

        compatible_pairs = {
            frozenset({"text", "list"}),
            frozenset({"text", "heading"}),
        }

        return frozenset({left, right}) in compatible_pairs

    @classmethod
    def _merge_metadata(
        cls,
        left: Self,
        right: Self,
    ) -> dict[str, Any]:
        left_source = left.metadata.get("source", {})
        right_source = right.metadata.get("source", {})

        pages = sorted(
            set(left_source.get("pages", [])) | set(right_source.get("pages", []))
        )

        source = {
            **left_source,
            "page": pages[0] if pages else left.page,
            "pages": pages,
            "provenance": {
                **left_source.get("provenance", {}),
                **right_source.get("provenance", {}),
            },
        }

        left_structure = left.metadata.get("structure", {})
        right_structure = right.metadata.get("structure", {})

        if left.heading_paths and left.heading_paths == right.heading_paths:
            heading_paths = left.heading_paths
        elif not left.heading_paths:
            heading_paths = right.heading_paths
        else:
            heading_paths = left.heading_paths

        structure = {
            **left_structure,
            "heading_paths": heading_paths,
            "source_items": (
                left_structure.get("source_items", [])
                + right_structure.get("source_items", [])
            ),
            "labels": (
                left_structure.get("labels", []) + right_structure.get("labels", [])
            ),
            "content_type": (
                left.content_type
                if left.content_type == right.content_type
                else "mixed"
            ),
        }

        return {
            **left.metadata,
            "source": source,
            "structure": structure,
        }
