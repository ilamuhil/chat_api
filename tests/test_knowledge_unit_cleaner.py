import unittest

from app.ingestion.knowledge_unit import ContentType, KnowledgeUnit, SourceType
from app.ingestion.knowledge_unit.ku_rules import KnowledgeUnitCleaner


class KnowledgeUnitCleanerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cleaner = KnowledgeUnitCleaner()

    def _unit(self, content: str) -> KnowledgeUnit:
        return KnowledgeUnit(source_type=SourceType.HTML, content=content)

    def test_removes_common_standalone_decorative_symbols(self) -> None:
        for content in ("—", "…", "▪", "●", "►", "⌄"):
            with self.subTest(content=content):
                self.assertIsNone(self.cleaner.clean(self._unit(content)))

    def test_removes_unambiguous_ui_labels_case_insensitively(self) -> None:
        labels = (
            "  SKIP   TO MAIN CONTENT  ",
            "Back to Top",
            "NEXT PAGE",
            "Accept All Cookies",
            "Copy Link to Clipboard",
            "Share on LinkedIn",
            "Please Wait…",
        )

        for content in labels:
            with self.subTest(content=content):
                self.assertIsNone(self.cleaner.clean(self._unit(content)))

    def test_preserves_potentially_meaningful_content(self) -> None:
        contents = (
            "Facebook",
            "Instagram",
            "Twitter",
            "Share capital requirements",
            "The next page explains the fee structure.",
            "Download requirements for students",
        )

        for content in contents:
            with self.subTest(content=content):
                cleaned = self.cleaner.clean(self._unit(content))
                self.assertIsNotNone(cleaned)
                assert cleaned is not None
                self.assertEqual(cleaned.content, content)

    def test_preserved_content_keeps_metadata_except_stale_token_count(self) -> None:
        unit = KnowledgeUnit(
            source_type=SourceType.HTML,
            content="  Admissions   information  ",
            metadata={
                "source": {"canonical_url": "https://example.edu/admissions"},
                "token_count": 99,
            },
        )

        cleaned = self.cleaner.clean(unit)

        self.assertIsNotNone(cleaned)
        assert cleaned is not None
        self.assertEqual(cleaned.content, "Admissions information")
        self.assertEqual(
            cleaned.metadata.get("source"),
            {"canonical_url": "https://example.edu/admissions"},
        )
        self.assertNotIn("token_count", cleaned.metadata)

    def test_code_blocks_keep_meaningful_whitespace(self) -> None:
        content = '    return "a  b"'
        unit = KnowledgeUnit(
            source_type=SourceType.MARKDOWN,
            content=content,
            metadata={"structure": {"content_type": ContentType.CODE}},
        )

        cleaned = self.cleaner.clean(unit)

        self.assertIsNotNone(cleaned)
        assert cleaned is not None
        self.assertEqual(cleaned.content, content)
        self.assertFalse(cleaned.is_splittable_prose)


if __name__ == "__main__":
    unittest.main()
