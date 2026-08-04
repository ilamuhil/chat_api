from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import httpx
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_community.document_loaders.text import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.helpers.rag import count_tokens, create_embeddings
from app.helpers.utils import clean_scraped_text, extract_main_text_from_html
from app.infra.r2_storage import r2_download_to_path, r2_object_exists
from app.models.chat_db_models import Documents, EmbeddingConfigurations
from app.models.dashboard_db_models import Files, TrainingSources

logger = logging.getLogger(__name__)

_MIME_TO_EXT: dict[str, str] = {
    "application/pdf": ".pdf",
    "text/csv": ".csv",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/x-markdown": ".md",
}


def _extension_for_loader(
    original_filename: str | None, mime_type: str | None
) -> str:
    """Resolve an extension from the original filename or MIME type."""
    if original_filename:
        extension = Path(original_filename).suffix.lower()
        if extension:
            return extension
    if mime_type:
        mime = mime_type.strip().lower().split(";")[0]
        return _MIME_TO_EXT.get(mime, "")
    return ""


def _loader_for_file(
    path: Path,
    original_filename: str | None = None,
    mime_type: str | None = None,
) -> PyPDFLoader | CSVLoader | TextLoader:
    """Create the appropriate LangChain loader for a downloaded source file."""
    extension = _extension_for_loader(original_filename, mime_type)
    logger.info(
        "Loading training file",
        extra={
            "path": str(path),
            "original_filename": original_filename,
            "mime_type": mime_type,
            "resolved_extension": extension,
        },
    )
    if extension == ".pdf":
        return PyPDFLoader(str(path))
    if extension == ".csv":
        return CSVLoader(file_path=str(path))
    if extension in {".md", ".txt"}:
        return TextLoader(str(path), encoding="utf-8", autodetect_encoding=True)
    raise ValueError(
        "Unsupported file type "
        f"(original_filename={original_filename!r}, mime_type={mime_type!r}). "
        "Supported: .csv .md .pdf .txt"
    )


def _chunk_text(text: str, config: EmbeddingConfigurations) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )
    return splitter.split_text(text)


def _persist_chunks(
    source: TrainingSources,
    chat_session: Session,
    chunks: list[str],
    config: EmbeddingConfigurations,
) -> None:
    """Persist inactive chunks, then create and activate their embeddings."""
    if source.bot_id is None or source.organization_id is None:
        raise ValueError("Training source missing bot_id/organization_id")

    try:
        documents = [
            Documents(
                organization_id=str(source.organization_id),
                bot_id=source.bot_id,
                source_id=source.id,
                embedding_configuration_id=config.id,
                chunk_index=index,
                content=chunk,
                is_active=False,
                token_count=count_tokens(chunk, config.model),
            )
            for index, chunk in enumerate(chunks)
        ]
        chat_session.add_all(documents)
        chat_session.commit()
    except Exception:
        chat_session.rollback()
        logger.exception(
            "Failed to persist training document chunks",
            extra={"source_id": str(source.id), "chunk_count": len(chunks)},
        )
        raise ValueError("Failed to save training data.")

    logger.info(
        "Document chunks persisted for training source",
        extra={"source_id": str(source.id), "chunk_count": len(chunks)},
    )

    persisted_documents = list(
        chat_session.scalars(
            select(Documents)
            .where(
                Documents.source_id == source.id,
                Documents.embedding_configuration_id == config.id,
                Documents.is_active.is_(False),
                Documents.deleted_at.is_(None),
            )
            .order_by(Documents.chunk_index)
        ).all()
    )
    create_embeddings(
        chat_session,
        persisted_documents,
        str(source.id),
        model=config.model,
        dimensions=config.dimension,
    )


def _load_url_text(url: str) -> str:
    parsed = httpx.URL(url)
    if not parsed.scheme or not parsed.host:
        raise ValueError(f"Invalid URL: {url}")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ChatAPI/1.0; +https://example.local)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(
            follow_redirects=True, timeout=30.0, headers=headers
        ) as client:
            response = client.get(url)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if content_type and "html" not in content_type:
            raise ValueError(f"Unsupported content-type: {content_type}")

        cleaned = clean_scraped_text(extract_main_text_from_html(response.text))
        if len(cleaned) >= 200:
            return cleaned
        raise ValueError("Page content too short after extraction/cleaning")
    except Exception as error:
        logger.warning(
            "Primary URL extraction failed; falling back to WebBaseLoader",
            extra={"url": url, "error": str(error)},
        )

    documents = WebBaseLoader(url).load()
    cleaned = clean_scraped_text(
        "\n\n".join(
            document.page_content
            for document in documents
            if getattr(document, "page_content", "")
        )
    )
    if len(cleaned) < 200:
        raise ValueError("Page content too short after fallback extraction/cleaning")
    return cleaned


def process_url_training_source(
    source: TrainingSources,
    chat_session: Session,
    config: EmbeddingConfigurations,
) -> None:
    """Load, chunk, persist, and embed a URL training source."""
    if not isinstance(source.source_value, str):
        raise ValueError("URL must be a string")
    _persist_chunks(source, chat_session, _chunk_text(_load_url_text(source.source_value), config), config)


def _load_file_text(
    source: TrainingSources, dashboard_session: Session
) -> str:
    if not source.source_value:
        raise ValueError("Missing file information for this training source.")

    file_path = str(Path(str(source.source_value)))
    try:
        file_record = dashboard_session.scalars(
            select(Files).where(Files.path == file_path)
        ).one_or_none()
    except Exception:
        logger.exception(
            "Failed to query file record for training source",
            extra={"source_id": str(source.id), "file_path": file_path},
        )
        raise ValueError("Unable to locate the uploaded file for this training source.")

    if file_record is None or not file_record.bucket or not file_record.path:
        raise ValueError("Uploaded file metadata is incomplete.")

    try:
        exists = r2_object_exists(file_record.bucket, file_record.path)
    except Exception:
        logger.exception(
            "Failed to check file existence in R2",
            extra={
                "source_id": str(source.id),
                "bucket": file_record.bucket,
                "path": file_record.path,
            },
        )
        raise ValueError("Unable to verify the file in storage right now. Please retry.")

    if not exists:
        raise ValueError("File upload was not completed. Please re-upload and try again.")

    suffix = _extension_for_loader(
        file_record.original_filename, file_record.mime_type
    )
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_path = Path(temporary_directory) / f"source{suffix}"
        try:
            r2_download_to_path(
                file_record.bucket, file_record.path, str(temporary_path)
            )
            loader = _loader_for_file(
                temporary_path,
                original_filename=file_record.original_filename,
                mime_type=file_record.mime_type,
            )
            documents = loader.load()
        except ValueError:
            raise
        except Exception:
            logger.exception(
                "Failed to download or parse training file",
                extra={
                    "source_id": str(source.id),
                    "bucket": file_record.bucket,
                    "path": file_record.path,
                },
            )
            raise ValueError(
                "Unable to read the uploaded file. Please try a different file."
            )

    cleaned = clean_scraped_text(
        "\n\n".join(
            document.page_content
            for document in documents
            if getattr(document, "page_content", "")
        )
    )
    if len(cleaned) < 50:
        raise ValueError("File content too short after loading the data from file")
    return cleaned


def process_file_training_source(
    source: TrainingSources,
    chat_session: Session,
    dashboard_session: Session,
    config: EmbeddingConfigurations,
) -> None:
    """Load, chunk, persist, and embed a file training source."""
    _persist_chunks(
        source,
        chat_session,
        _chunk_text(_load_file_text(source, dashboard_session), config),
        config,
    )
