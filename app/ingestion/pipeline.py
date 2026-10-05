from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.infra.r2_storage import r2_download_to_path, r2_object_exists
from app.ingestion.csv_ingestion.csv_parser import CsvPipeline
from app.ingestion.errors import TrainingFailure, error_stage
from app.ingestion.html_ingestion.pipeline import HtmlIngestionPipeline
from app.ingestion.knowledge_unit import ChunkingConfig, KnowledgeUnit, SourceType
from app.ingestion.markdown_ingestion.markdown_parser import MarkdownUnitParser
from app.ingestion.txt_ingestion.txt_pipeline import TxtPipeline
from app.models.chat_db_models import Documents, EmbeddingConfigurations
from app.models.dashboard_db_models import Files, TrainingSources
from app.rag.embeddings import create_embeddings

logger = logging.getLogger(__name__)

_MIME_TO_EXT = {
    "application/pdf": ".pdf",
    "text/csv": ".csv",
    "application/csv": ".csv",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
_SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".csv",
    ".txt",
    ".md",
    ".markdown",
    ".docx",
    ".html",
    ".htm",
}


def resolve_file_extension(filename: str | None, mime_type: str | None) -> str:
    """Prefer the upload's filename, falling back to its declared MIME type."""
    extension = Path(filename).suffix.lower() if filename else ""
    if not extension and mime_type:
        extension = _MIME_TO_EXT.get(mime_type.split(";", 1)[0].strip().lower(), "")
    if extension not in _SUPPORTED_EXTENSIONS:
        raise TrainingFailure(
            "unsupported_file_type",
            "Unsupported file type. Supported: CSV, DOCX, HTML, Markdown, PDF and TXT.",
            action="Upload the file in a supported format.",
            retryable=False,
        )
    return extension


class IngestionPipeline:
    """Extract a source into knowledge units, consolidate, persist, and embed."""

    def __init__(
        self,
        source: TrainingSources,
        chat_session: Session,
        dashboard_session: Session,
        config: EmbeddingConfigurations,
    ):
        self.source = source
        self.chat_session = chat_session
        self.dashboard_session = dashboard_session
        self.config = config
        with error_stage("configuration"):
            self.chunking_config = ChunkingConfig(
                min_tokens=config.min_chunk_tokens,
                target_tokens=config.target_chunk_tokens,
                max_tokens=config.max_chunk_tokens,
            )

    def run(self) -> None:
        source = self.source
        if source.bot_id is None or source.organization_id is None:
            raise TrainingFailure(
                "source_metadata_missing",
                "The source is missing its bot or organization information.",
                action="Ask an administrator to correct the source details.",
                retryable=False,
            )
        if source.deleted_at is not None:
            raise TrainingFailure(
                "source_deleted",
                "This source has been deleted.",
                action="Add the source again to train it.",
                retryable=False,
            )
        if source.bot_id != self.config.bot_id:
            raise TrainingFailure(
                "configuration_mismatch",
                "The selected model settings belong to a different bot.",
                stage="configuration",
                action="Select the correct bot and retry training.",
                retryable=False,
            )
        if self.config.provider.lower() != "openai":
            raise TrainingFailure(
                "unsupported_embedding_provider",
                "The selected embedding provider is not supported.",
                stage="configuration",
                action="Select a supported embedding provider and retry.",
                retryable=False,
            )

        if source.type == "url":
            with error_stage("extraction"):
                units = HtmlIngestionPipeline(self.config.model).run(
                    source.source_value or ""
                )
        elif source.type == "file":
            units = self._extract_file()
        else:
            raise TrainingFailure(
                "unsupported_source_type",
                "Unsupported training source type. Add a URL or a supported file.",
                retryable=False,
            )

        for unit in units:
            metadata = unit.metadata.setdefault("source", {})
            metadata["source_id"] = str(source.id)
            if source.type == "url":
                metadata["url"] = source.source_value

        with error_stage("processing"):
            units = KnowledgeUnit.consolidate(
                units, self.config.model, self.chunking_config
            )
            if not units:
                raise ValueError("Source produced no usable knowledge units")
            documents = KnowledgeUnit.to_documents_adapter(
                units,
                organization_id=source.organization_id,
                bot_id=source.bot_id,
                source_id=source.id,
                embedding_configuration_id=self.config.id,
                embedding_model=self.config.model,
            )
        self._persist_documents(documents)

    def _extract_file(self) -> list[KnowledgeUnit]:
        source = self.source
        if not source.source_value:
            raise TrainingFailure(
                "file_reference_missing",
                "The source does not reference an uploaded file.",
                action="Upload the file again.",
                retryable=False,
            )
        with error_stage("storage_lookup"):
            file_record = self.dashboard_session.scalars(
                select(Files).where(
                    Files.path == source.source_value,
                    Files.organization_id == source.organization_id,
                    Files.bot_id == source.bot_id,
                    Files.deleted_at.is_(None),
                )
            ).one_or_none()
        if file_record is None or not file_record.bucket or not file_record.path:
            raise TrainingFailure(
                "file_metadata_missing",
                "The uploaded file information is missing or incomplete.",
                stage="storage_lookup",
                action="Upload the file again.",
                retryable=False,
            )
        extension = resolve_file_extension(
            file_record.original_filename, file_record.mime_type
        )
        filename = file_record.original_filename or f"source{extension}"
        with error_stage("storage_check"):
            if not r2_object_exists(file_record.bucket, file_record.path):
                raise TrainingFailure(
                    "file_upload_missing",
                    "File upload was not completed. Please re-upload and try again.",
                    stage="storage_check",
                    action="Upload the file again.",
                    retryable=False,
                )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"source{extension}"
            with error_stage("download"):
                r2_download_to_path(file_record.bucket, file_record.path, str(path))
            with error_stage("extraction"):
                units = self._parse_file(path, filename)

        # CSV parsers see a temporary storage filename; keep the upload attribution.
        for unit in units:
            unit.metadata.setdefault("source", {})["filename"] = filename
        return units

    def _parse_file(self, path: Path, filename: str) -> list[KnowledgeUnit]:
        match path.suffix.lower():
            case ".pdf":
                from app.ingestion.converter_factory import DocumentConverterFactory
                from app.ingestion.pdf_ingestion.pdf_extractor import PdfExtractor
                from app.ingestion.pdf_ingestion.pdf_parser import PdfParser

                document = PdfExtractor(
                    DocumentConverterFactory.get_converter()
                ).convert_pdf(path)
                return PdfParser(
                    self.config.max_chunk_tokens, self.config.model
                ).chunk_pdf(document, filename=filename)
            case ".docx":
                from app.ingestion.converter_factory import DocumentConverterFactory
                from app.ingestion.docx_ingestion.docx_extractor import DocxExtractor

                return DocxExtractor(DocumentConverterFactory.get_converter()).convert(
                    path, self.config.model, filename
                )
            case ".csv":
                return CsvPipeline(
                    min_tokens=self.chunking_config.min_tokens,
                    target_tokens=self.chunking_config.target_tokens,
                    max_tokens=self.chunking_config.max_tokens,
                    embedding_model=self.config.model,
                ).extract_csv(path)
            case ".txt":
                return TxtPipeline().convert(path, filename)
            case ".md" | ".markdown":
                return MarkdownUnitParser(self.config.model).parse(
                    path.read_text(encoding="utf-8-sig"),
                    SourceType.MARKDOWN,
                    source_filename=filename,
                )
            case ".html" | ".htm":
                return HtmlIngestionPipeline(self.config.model).parse_html(
                    path.read_text(encoding="utf-8-sig"), filename=filename
                )
            case _:
                raise ValueError(f"Unsupported file extension: {path.suffix}")

    def _persist_documents(self, documents: list[Documents]) -> None:
        """Replace this source/configuration atomically, including on retries."""
        try:
            with error_stage("persistence"):
                # Embeddings are removed by the document foreign key's ON DELETE CASCADE.
                self.chat_session.execute(
                    delete(Documents).where(
                        Documents.source_id == self.source.id,
                        Documents.bot_id == self.source.bot_id,
                        Documents.organization_id == self.source.organization_id,
                        Documents.embedding_configuration_id == self.config.id,
                    )
                )
                self.chat_session.add_all(documents)
                self.chat_session.flush()
            with error_stage("embedding"):
                create_embeddings(
                    self.chat_session,
                    documents,
                    str(self.source.id),
                    model=self.config.model,
                    dimensions=self.config.dimension,
                )
            with error_stage("persistence"):
                self.chat_session.commit()
        except Exception:
            self.chat_session.rollback()
            logger.exception(
                "Failed to persist knowledge units and embeddings",
                extra={"source_id": str(self.source.id)},
            )
            raise
