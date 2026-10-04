from pathlib import Path

from docling.datamodel.base_models import ConversionStatus
from docling.document_converter import DocumentConverter

from app.ingestion.knowledge_unit import KnowledgeUnit, SourceType
from app.ingestion.markdown_ingestion.markdown_parser import MarkdownUnitParser


class DocxExtractor:
    """Extracts content from a DOCX file."""

    converter: DocumentConverter

    def __init__(self, converter: DocumentConverter):
        self.converter = converter

    def validate_docx_path(self, file_path: str | Path) -> Path:
        docx_path = Path(file_path)
        if not docx_path.exists():
            raise FileNotFoundError(f"File {docx_path} does not exist")
        if not docx_path.is_file():
            raise ValueError(f"File {docx_path} is not a file")
        if docx_path.suffix.lower() != ".docx":
            raise ValueError(f"File {docx_path} is not a DOCX file")
        return docx_path

    def convert(
        self, file_path: str | Path, embedding_model: str, filename: str
    ) -> list[KnowledgeUnit]:
        docx_path = self.validate_docx_path(file_path)
        result = self.converter.convert(docx_path)
        if result.status != ConversionStatus.SUCCESS:
            raise RuntimeError(
                f"DOCX conversion incomplete for {docx_path.name}: "
                f"{result.status}; errors={result.errors}"
            )
        markdown = result.document.export_to_markdown()
        markdown_parser = MarkdownUnitParser(embedding_model=embedding_model)
        units = markdown_parser.parse(
            markdown,
            source_type=SourceType.DOCX,
            source_filename=filename,
        )

        return units
