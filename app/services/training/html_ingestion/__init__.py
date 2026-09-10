"""HTML ingestion pipeline for training data."""

from app.services.training.html_ingestion.html_cleaner import HTMLCleaner
from app.services.training.html_ingestion.markdown_converter import MarkdownConverter
from app.services.training.html_ingestion.markdown_parser import MarkdownUnitParser
from app.services.training.html_ingestion.pipeline import HtmlIngestionPipeline

# this exposes all the classes and functions in the html_ingestion module
# no need to import the classes from individual files

__all__ = [
    "HTMLCleaner",
    "HtmlIngestionPipeline",
    "MarkdownConverter",
    "MarkdownUnitParser",
]
