import logging
from dataclasses import dataclass, field

from app.ingestion.html_ingestion.html_cleaner import HTMLCleaner
from app.ingestion.html_ingestion.html_extractor import HtmlExtractor
from app.ingestion.knowledge_unit import KnowledgeUnit, SourceType
from app.ingestion.markdown_ingestion.markdown_converter import MarkdownConverter
from app.ingestion.markdown_ingestion.markdown_parser import MarkdownUnitParser

logger = logging.getLogger(__name__)


# slots: Instances use less memory,
# slots: Attribute access can be slightly faster,
# slots: Arbitrary new attributes are prevented


@dataclass()
class HtmlIngestionPipeline:
    embedding_model: str
    html_extractor: HtmlExtractor = field(default_factory=HtmlExtractor)
    html_cleaner: HTMLCleaner = field(default_factory=HTMLCleaner)
    markdown_converter: MarkdownConverter = field(default_factory=MarkdownConverter)
    markdown_unit_parser: MarkdownUnitParser | None = None

    def __post_init__(self) -> None:
        if self.markdown_unit_parser is None:
            self.markdown_unit_parser = MarkdownUnitParser(
                embedding_model=self.embedding_model
            )

    def run(self, url: str) -> list[KnowledgeUnit]:
        html = self.html_extractor.extract(url)
        units = self.parse_html(html)
        for unit in units:
            unit.metadata.setdefault("source", {})["url"] = url
        return units

    def parse_html(
        self, html: str, *, filename: str | None = None
    ) -> list[KnowledgeUnit]:
        """Use the same cleaner and parser for fetched pages and uploaded HTML."""
        root, page_meta = self.html_cleaner.clean(html)
        if not root:
            raise ValueError("Minimum content requirement of 200 characters not met")
        markdown = self.markdown_converter.convert(root)
        # HTML source attribution remains in canonical_url when the page provides it.
        parser = self.markdown_unit_parser
        if parser is None:
            raise RuntimeError("Markdown unit parser was not initialized.")
        return parser.parse(
            markdown, SourceType.HTML, page_meta, source_filename=filename
        )
