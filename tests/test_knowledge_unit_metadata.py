import unittest

from app.ingestion.knowledge_unit import (
    ChunkingConfig,
    ContentType,
    KnowledgeUnit,
    SourceType,
)
from app.ingestion.knowledge_unit.ku_merger import KnowledgeUnitMerger
from app.ingestion.markdown_ingestion.markdown_parser import MarkdownUnitParser


class KnowledgeUnitMetadataTests(unittest.TestCase):
    def test_markdown_units_use_canonical_types_and_heading_paths(self) -> None:
        parser = MarkdownUnitParser(embedding_model="text-embedding-3-small")

        units = parser.parse(
            "# Admissions\n\nStudents must apply online.\n\n- One\n- Two",
            SourceType.MARKDOWN,
        )

        self.assertEqual(
            [unit.content_type for unit in units],
            [ContentType.TEXT, ContentType.LIST],
        )
        self.assertEqual(units[0].heading_paths, ["Admissions"])
        self.assertEqual(units[1].heading_paths, ["Admissions"])

    def test_merging_different_types_records_component_types(self) -> None:
        left = KnowledgeUnit(
            source_type=SourceType.PDF,
            content="Admissions",
            metadata={
                "structure": {
                    "content_type": ContentType.TEXT,
                    "heading_paths": ["Overview"],
                }
            },
        )
        right = KnowledgeUnit(
            source_type=SourceType.PDF,
            content="Applications",
            metadata={
                "structure": {
                    "content_type": ContentType.HEADING,
                    "heading_paths": ["Overview"],
                }
            },
        )

        merger = KnowledgeUnitMerger(
            ChunkingConfig(min_tokens=1, max_tokens=100, target_tokens=50),
            tokens=len,
        )
        metadata = merger.merge_metadata(left, right)
        structure = metadata.get("structure")
        self.assertIsNotNone(structure)
        assert structure is not None

        self.assertEqual(structure.get("content_type"), ContentType.MIXED)
        self.assertEqual(
            structure.get("content_types"),
            [ContentType.TEXT, ContentType.HEADING],
        )


if __name__ == "__main__":
    unittest.main()
