import logging
from dataclasses import dataclass, field

from app.services.training.html_ingestion.html_cleaner import HTMLCleaner
from app.services.training.html_ingestion.markdown_converter import MarkdownConverter
from app.services.training.html_ingestion.markdown_parser import MarkdownUnitParser
from app.services.training.knowledge_unit import KnowledgeUnit

logger = logging.getLogger(__name__)


# slots: Instances use less memory,
# slots: Attribute access can be slightly faster,
# slots: Arbitrary new attributes are prevented


@dataclass()
class HtmlIngestionPipeline:
    html_cleaner: HTMLCleaner = field(default_factory=HTMLCleaner)
    markdown_converter: MarkdownConverter = field(default_factory=MarkdownConverter)
    markdown_unit_parser: MarkdownUnitParser = field(default_factory=MarkdownUnitParser)

    def run(self, html: str) -> list[KnowledgeUnit]:
        root, _page_meta = self.html_cleaner.clean(html)
        if not root:
            raise ValueError("Minimum content requirement of 200 characters not met")
        markdown = self.markdown_converter.convert(root)
        return self.markdown_unit_parser.parse(markdown, _page_meta)
