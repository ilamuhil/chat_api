from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

import httpx
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_community.document_loaders.text import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.session import DashboardDbSessionLocal, SessionLocal
from app.helpers.rag import count_tokens, create_embeddings
from app.helpers.utils import (clean_scraped_text, delete_file_from_storage,
                               extract_main_text_from_html)
from app.infra.r2_storage import r2_download_to_path, r2_object_exists
from app.models.chat_db_models import Documents, Embeddings, TrainingJobs
from app.models.dashboard_db_models import Files, TrainingSources

logger = logging.getLogger(__name__)


def process_url_training_source(
    source: TrainingSources,
    py_session: Session,
    chunk_config: dict | None = None,
) -> None:
    """
    - Verifies if the source is a valid URL
    - Fetches the HTML and extracts the main content and cleans it falls back to WebBaseLoader if the main text falls short of the threshold.
    - Chunks the content for RAG and persists to chat_db.documents
    """
    if chunk_config is None:
        chunk_config = {"chunk_size": 800, "chunk_overlap": 100}

    url = source.source_value
    if source.bot_id is None or source.organization_id is None:
        logger.error("Training source missing bot_id/organization_id")
        raise ValueError("Training source missing bot_id/organization_id")
    # verify if the source is a valid url
    assert isinstance(url, str), "URL must be a string"
    res = urlparse(url)
    if not all([res.scheme, res.netloc]):
        source.status = "failed"
        source.error_message = f"Invalid URL: {url} format"
        py_session.commit()
        py_session.refresh(source)
        logger.error(f"Invalid URL: {url}")
        raise ValueError(f"Invalid URL: {url}")

    cleaned: str | None = None

    # * Fetch the HTML and extract the main content and clean it
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; ChatAPI/1.0; +https://example.local)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            logger.error(f"Failed to fetch URL", extra={
                         "url": url, "status_code": resp.status_code})
            raise ValueError(
                f"Failed to fetch URL (status={resp.status_code})")
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            if content_type and "html" not in content_type:
                logger.error(f"Unsupported content-type: {content_type}")
                raise ValueError(f"Unsupported content-type: {content_type}")

        raw_text = extract_main_text_from_html(resp.text)
        cleaned = clean_scraped_text(raw_text)
        if len(cleaned) < 200:
            logger.error(f"Page content too short after extraction/cleaning",
                         extra={"url": url, "content_length": len(cleaned)})
            raise ValueError(
                "Page content too short after extraction/cleaning")
    except Exception as e:
        logger.error(f"Primary URL extraction failed, falling back to WebBaseLoader", extra={
                     "url": url, "error": str(e)})
        cleaned = None

    # Fallback: LangChain WebBaseLoader extraction.
    # WebBaseLoader is generic doesn't remove boilerplate html elements doesnt remove scripts or styles. So it is used as a fallback mechanism when the main text falls short of the threshold.
    if cleaned is None:
        loader = WebBaseLoader(url)
        docs = loader.load()
        merged = "\n\n".join(
            [d.page_content for d in docs if getattr(d, "page_content", "")])
        cleaned = clean_scraped_text(merged)
        if len(cleaned) < 200:
            logger.error(f"Page content too short after fallback extraction/cleaning",
                         extra={"url": url, "content_length": len(cleaned)})
            raise ValueError(
                "Page content too short after fallback extraction/cleaning")

    # Chunk for RAG and persist to chat.documents
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=int(chunk_config.get("chunk_size", 800)),
        chunk_overlap=int(chunk_config.get("chunk_overlap", 100)),
    )
    chunks = splitter.split_text(cleaned)
    for i, chunk in enumerate[str](chunks):
        py_session.add(
            Documents(
                organization_id=str(source.organization_id),
                bot_id=source.bot_id,
                source_id=source.id,
                chunk_index=i,
                content=chunk,
                is_active=False,
                chunk_size=int(chunk_config.get("chunk_size", 800)),
                chunk_overlap=int(chunk_config.get("chunk_overlap", 100)),
                token_count=count_tokens(chunk, "text-embedding-3-small"),
                embedding_model="text-embedding-3-small",
                embedding_version="v.1.0.0",
                embedding_provider="openai",
            )
        )
    try:
        py_session.commit()
    except Exception:
        logger.exception(
            "Failed to persist document chunks",
            extra={"source_id": str(source.id), "chunk_count": len(chunks)},
        )
        raise ValueError("Failed to save training data.")
    
    logger.info(f"Chunks persisted for source", extra={"source_id": str(source.id), "chunk_count": len(chunks)})
    
    # Create embeddings for the chunks
    documents = list[Documents](py_session.scalars(select(Documents).where(Documents.source_id == source.id,Documents.is_active == False).order_by(Documents.chunk_index)).all())
    create_embeddings(py_session, documents,str(source.id))
    
    


def _loader_for_path(path: Path):
    ext = path.suffix.lower()
    if ext == ".pdf":
        return PyPDFLoader(str(path))
    if ext == ".csv":
        return CSVLoader(file_path=str(path))
    if ext in (".md", ".txt"):
        # Treat markdown as text for now (keeps deps light).
        return TextLoader(str(path), encoding="utf-8", autodetect_encoding=True)
    raise ValueError(
        f"Unsupported file type: {ext}. Supported: .csv .md .pdf .txt")


def process_file_training_source(
    source: TrainingSources, py_session: Session, chunk_config: dict | None = None
) -> None:
    if chunk_config is None:
        chunk_config = {"chunk_size": 800, "chunk_overlap": 100}
    

    # Find the file record so we know its storage path (and extension).
    if not source.source_value:
        logger.error(
            "Training source missing source_value",
            extra={"source_id": str(source.id), "type": getattr(source, "type", None)},
        )
        raise ValueError("Missing file information for this training source.")

    # `source_value` for file sources is expected to be the object key/path.
    file_path = str(Path(str(source.source_value)))
    file_record: Files | None = None
    try:
        file_record = py_session.scalars(
            select(Files).where(Files.path == file_path)
        ).one_or_none()
    except Exception:
        logger.exception(
            "Failed to query file record for training source",
            extra={"source_id": str(source.id), "file_path": file_path},
        )
        raise ValueError("Unable to locate the uploaded file for this training source.")
    
    if file_record is None or file_record.bucket is None or file_record.path is None:
        logger.error(
            "File record missing bucket or path",
            extra={"source_id": str(source.id), "file_record": repr(file_record)},
        )
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
        logger.error(
            "File not found in storage",
            extra={
                "source_id": str(source.id),
                "bucket": file_record.bucket,
                "path": file_record.path,
            },
        )
        raise ValueError("File upload was not completed. Please re-upload and try again.")

    # Download to a temp file, then use LangChain loaders (csv/md/pdf/txt).
    suffix = Path(file_record.path).suffix or ""
    cleaned = ""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / f"source{suffix}"
        try:
            r2_download_to_path(file_record.bucket, file_record.path, str(tmp_path))
        except Exception:
            logger.exception(
                "Failed to download file from R2",
                extra={
                    "source_id": str(source.id),
                    "bucket": file_record.bucket,
                    "path": file_record.path,
                },
            )
            raise ValueError("Failed to download the uploaded file")

        try:
            loader = _loader_for_path(tmp_path)
            docs = loader.load()
            merged = "\n\n".join(
                [d.page_content for d in docs if getattr(d, "page_content", "")]
            )
            cleaned = clean_scraped_text(merged)
        except ValueError:
            # Keep user-facing message from loader selection (unsupported type, etc.).
            raise
        except Exception:
            logger.exception(
                "Failed to parse uploaded file",
                extra={
                    "source_id": str(source.id),
                    "bucket": file_record.bucket,
                    "path": file_record.path,
                    "tmp_path": str(tmp_path),
                },
            )
            raise ValueError("Unable to read the uploaded file. Please try a different file.")
    if len(cleaned) < 50:
        logger.error(
            "File content too short after loading/cleaning",
            extra={"source_id": str(source.id), "content_length": len(cleaned)},
        )
        raise ValueError("File content too short after loading the data from file")

    splitter = RecursiveCharacterTextSplitter(chunk_size=int(chunk_config.get(
        "chunk_size", 800)), chunk_overlap=int(chunk_config.get("chunk_overlap", 100)))
    chunks = splitter.split_text(cleaned)
    try:
        with py_session.begin():
            for i, chunk in enumerate[str](chunks):
                py_session.add(
                    Documents(
                        organization_id=str(source.organization_id),
                        bot_id=source.bot_id,
                        source_id=source.id,
                        chunk_index=i,
                        content=chunk,
                        embedding_model="text-embedding-3-small",
                        embedding_version="v.1.0.0",
                        embedding_provider="openai",
                        is_active=False,
                        chunk_size=int(chunk_config.get("chunk_size", 800)),
                        chunk_overlap=int(chunk_config.get("chunk_overlap", 100)),
                        token_count=count_tokens(chunk, "text-embedding-3-small")
                    )
                )
                #TODO: Implement versioning logic for the embeddings
                # Versioning scheme:
                # PATCH (v1.0.1): metadata-only changes
                # MINOR (v1.1.0): chunking changes
                # MAJOR (v2.0.0): model or dimension changes
        logger.info(f"Document chunks persisted for source: {source.id}")
    except Exception:
        logger.exception(
            "Failed to persist document chunks",
            extra={"source_id": str(source.id), "chunk_count": len(chunks)},
        )
        raise ValueError("Failed to save training data. Please retry.")
    logger.info(f"Document chunks persisted for training source",extra={"source_id": str(source.id), "chunk_count": len(chunks)})
    
    
    # Create embeddings for the chunks
    documents = list[Documents](py_session.scalars(select(Documents).where(Documents.source_id == source.id,Documents.is_active == False).order_by(Documents.chunk_index)).all())
    create_embeddings(py_session, documents,str(source.id))
    

def process_training_job(
    job_id: str,
    bot_id: str,
    organization_id: str,
    source_ids: Sequence[str],
) -> None:
    if SessionLocal is None or DashboardDbSessionLocal is None:
        logger.critical("Database sessions not configured")
        return

    chat_session = SessionLocal()
    dashboard_session = DashboardDbSessionLocal()

    job = None
    any_failed = False
    any_successful = False

    try:
        job_uuid = uuid.UUID(job_id)
        bot_uuid = uuid.UUID(str(bot_id))

        # ---- Load job ----
        job = chat_session.scalars(
            select(TrainingJobs).where(TrainingJobs.id == job_uuid)
        ).one()

        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        chat_session.commit()

        # ---- Load sources (scoped) ----
        source_uuids = [uuid.UUID(sid) for sid in source_ids]

        sources = dashboard_session.scalars(
            select(TrainingSources).where(
                TrainingSources.id.in_(source_uuids),
                TrainingSources.bot_id == bot_uuid,
                TrainingSources.organization_id == organization_id,
            )
        ).all()

        for source in sources:
            # ---- Idempotency guard ----
            try:
                # ---- Mark source processing ----
                source.status = "training"
                dashboard_session.commit()

                logger.info(
                    "Processing training source",
                    extra={"job_id": job_id, "source_id": str(source.id)},
                )

                if source.type == "url":
                    process_url_training_source(source, chat_session)
                else:
                    process_file_training_source(
                        source,
                        dashboard_session,
                        chunk_config={"chunk_size": 800, "chunk_overlap": 100},
                    )

                source.status = "trained"
                dashboard_session.commit()
                any_successful = True
            except Exception as e:
                any_failed = True
                logger.error(
                    "Failed to process training source",
                    extra={
                        "job_id": job_id,
                        "source_id": str(source.id),
                        "error": str(e),
                    },
                )
                dashboard_session.rollback()
                source.status = "training_failed"
                source.error_message = str(e)
                dashboard_session.commit()

        # ---- Final job status ----
        job.status = "completed" if any_successful and not any_failed else "partially_completed" if any_successful and any_failed else "failed"
        
        job.completed_at = datetime.now(timezone.utc)
        chat_session.commit()
        logger.info(f"Job status updated to {job.status}")

        logger.info(
            "Training job finished",
            extra={"job_id": job_id, "status": job.status},
        )

    except Exception as e:
        logger.exception(
            "Training job crashed",
            extra={"job_id": job_id, "error": str(e)},
        )
        chat_session.rollback()
        dashboard_session.rollback()

        if job is not None:
            try:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                chat_session.commit()
            except Exception:
                chat_session.rollback()

    finally:
        dashboard_session.close()
        chat_session.close()


def delete_training_source_job(job_id: str, source_id: str, organization_id: str, bot_id: str):
    """
    The following tasks are performed:
    1. Hard delete the files from R2 storage bucket if source is of type file
    2. Hard delete files row from the DB if source is of type file
    3. Hard delete the embeddings from the embeddings table in chat_db
    4. Hard delete the documents chunks from the documents table in chat_db
    5. Soft delete the training_jobs record in chat_db
    6. Soft delete the training_sources record in chat_db mark status as "purged" -> "deleted" status is used to track training_source records that are not gone through the deletion job process. It marks ui intent only.
    """
    py_session = SessionLocal()
    if py_session is None:
        logger.critical("Database session not Configured")
        return
    try:
        job_uuid = uuid.UUID(str(job_id))
        bot_uuid = uuid.UUID(str(bot_id))
        job = py_session.scalars(
            select(TrainingJobs)
            .where(TrainingJobs.id == job_uuid)
            .where(TrainingJobs.organization_id == organization_id)
            .where(TrainingJobs.bot_id == bot_uuid)
        ).one_or_none()
        source_uuid = uuid.UUID(source_id)
        source = py_session.scalars(
            select(TrainingSources)
            .where(TrainingSources.id == source_uuid)
            .where(TrainingSources.status == "deleted")
            .where(TrainingSources.organization_id == organization_id)
            .where(TrainingSources.bot_id == bot_uuid)
            .with_for_update()
        ).one_or_none()
        if not job:
            logger.error("job not found for id", extra={"job_id": job_id})
            raise ValueError(f"job not found for id: {job_id}")
        if source is None:
            logger.info("Source already claimed or not eligible for deletion", extra={
                        "source_id": source_id})
            return
        source.status = "purging"
        py_session.commit()
        logger.info(f"source status updated to purging: {source_id}")

        with py_session.begin():
            documents = py_session.scalars(select(Documents).where(
                Documents.source_id == source.id)).all()
            document_ids = [document.id for document in documents]
            if document_ids:
                embeddings = py_session.scalars(select(Embeddings).where(
                    Embeddings.document_id.in_(document_ids))).all()
                py_session.execute(delete(Embeddings).where(
                    Embeddings.document_id.in_(document_ids)))
                py_session.execute(delete(Documents).where(
                    Documents.source_id == source.id))
                logger.info(f"embeddings deleted from chat_db", extra={
                            "deleted_embeddings": len(embeddings)})
                logger.info(f"documents deleted from chat_db",
                            extra={"deleted_documents": len(documents)})
            job.status = "cleanup_completed"
            job.completed_at = datetime.now(timezone.utc)
            py_session.commit()
            logger.info(f"job status updated to deleted: {job_id}")
        if source.type == "file":
            file_uuid = uuid.UUID(str(source.source_value))
            file_record = py_session.scalars(
                select(Files).where(Files.id == file_uuid)).one_or_none()
            if file_record is None:
                logger.warning("file record not found for source", extra={
                    "source_id": source_id, "file_uuid": source.source_value})
                raise ValueError(
                    f"file record not found for id: {source.source_value}")
            if file_record.bucket is None or file_record.path is None:
                logger.warning("file record missing bucket or path", extra={
                    "source_id": source_id, "file_uuid": source.source_value})
                raise ValueError(
                    f"file record missing bucket or path: {source.source_value}")
            else:
                try:
                    delete_file_from_storage(
                        file_record.bucket, file_record.path)
                    logger.info(f"file deleted from storage:", extra={
                                "file_id": source.source_value, "file_path": file_record.path, "bucket": file_record.bucket})
                    py_session.delete(file_record)
                    logger.info(f"file record deleted from sb_db: {source.source_value}", extra={
                                "file_id": source.source_value, "file_path": file_record.path, "bucket": file_record.bucket})
                    py_session.commit()
                except Exception as e:
                    py_session.rollback()
                    logger.warning(
                        "Storage delete failed; file metadata retained for retry",
                        extra={
                            "file_id": file_record.id,
                            "bucket": file_record.bucket,
                            "path": file_record.path,
                            "error": str(e),
                        },
                    )

        source.status = "purged"
        py_session.commit()
        logger.info(f"source status updated to purged: {source_id}")
    except Exception as e:
        logger.error(f"Failed to delete training source", extra={
                     "source_id": source_id, "error": str(e)})
        if source is not None:
            source.status = "purge_failed"
            py_session.commit()
            logger.info(f"source status updated to purge_failed: {source_id}")
        raise e
    finally:
        py_session.close()
