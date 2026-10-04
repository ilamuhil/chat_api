import re
import unicodedata
from copy import deepcopy
from dataclasses import replace
from typing import ClassVar

from .knowledge_unit import KnowledgeUnit, WeakReason


class KnowledgeUnitCleaner:
    """Apply the existing cleanup and junk-removal rules."""

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

    def clean_content(self, content: str) -> str:
        content = self.CONTROL_CHAR_PATTERN.sub("", content)

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

    def clean(self, unit: KnowledgeUnit) -> KnowledgeUnit | None:
        content = self.clean_content(unit.content)
        if self._is_junk(content):
            return None
        metadata = deepcopy(unit.metadata)
        metadata.pop("token_count", None)
        return replace(unit, content=content, metadata=metadata)


class KnowledgeUnitWeaknessDetector:
    """Classify weaknesses using a fresh token count supplied by the processor."""

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

    def detect(
        self, unit: KnowledgeUnit, token_count: int, min_tokens: int
    ) -> set[WeakReason]:
        reasons: set[WeakReason] = set()
        content = unit.content.strip()
        if token_count < min_tokens:
            reasons.add(WeakReason.SHORT)
        if unit.content_type == "heading":
            reasons.add(WeakReason.HEADING_ONLY)
        if self._looks_like_broken_sentence(content):
            reasons.add(WeakReason.BROKEN_SENTENCE)
        if self._looks_context_dependent(content):
            reasons.add(WeakReason.CONTEXT_DEPENDENT)
        structure = unit.metadata.get("structure", {})
        question, answer = structure.get("question"), structure.get("answer")
        if (question is not None or answer is not None) and (
            not question or not answer
        ):
            reasons.add(WeakReason.ORPHAN_FAQ)
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
