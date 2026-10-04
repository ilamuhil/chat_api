"""HTML ingestion pipeline for training data."""

from app.ingestion.html_ingestion.html_cleaner import HTMLCleaner
from app.ingestion.html_ingestion.html_extractor import HtmlExtractor
from app.ingestion.html_ingestion.pipeline import HtmlIngestionPipeline
from app.ingestion.markdown_ingestion.markdown_converter import MarkdownConverter
from app.ingestion.markdown_ingestion.markdown_parser import MarkdownUnitParser

# this exposes all the classes and functions in the html_ingestion module
# no need to import the classes from individual files

__all__ = [
    "HTMLCleaner",
    "HtmlExtractor",
    "HtmlIngestionPipeline",
    "MarkdownConverter",
    "MarkdownUnitParser",
]
