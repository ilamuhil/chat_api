from __future__ import annotations

import uuid
from typing import Sequence
from urllib.parse import urlparse
from app.helpers.utils import extract_main_text_from_html, clean_scraped_text, get_signed_file_url
import httpx
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_community.document_loaders.text import TextLoader
from langchain_community.document_loaders.pdf import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tempfile
from pathlib import Path
from sqlalchemy import select
from app.models.supabase import Files, TrainingSources
from datetime import datetime, timezone
from app.db.session import SessionLocal, SupabaseSessionLocal
from sqlalchemy.orm import Session
from app.models.python_chat import Documents, TrainingJobs






def process_url_training_source(
  source: TrainingSources,
  sb_session: Session,
  py_session: Session,
  chunk_config: dict | None = None,
) -> None:
  """
  - Verifies if the source is a valid URL
  - Fetches the HTML and extracts the main content and cleans it falls back to WebBaseLoader if the main text falls short of the threshold.
  - Chunks the content for RAG and persists to python_chat_db.documents
  """
  if chunk_config is None:
    chunk_config = {"chunk_size": 800, "chunk_overlap": 100}
  
  url = source.source_value
  if source.bot_id is None or source.organization_id is None:
    raise ValueError("Training source missing bot_id/organization_id")
  #verify if the source is a valid url
  res = urlparse(url)
  if not all([res.scheme, res.netloc]):
    source.status = "failed"
    source.error_message = f"Invalid URL: {url} format"
    sb_session.commit()
    sb_session.refresh(source)
    raise ValueError(f"Invalid URL: {url}")
  
  cleaned: str | None = None

  #* Fetch the HTML and extract the main content and clean it
  try:
    headers = {
      "User-Agent": "Mozilla/5.0 (compatible; ChatAPI/1.0; +https://example.local)",
      "Accept": "text/html,application/xhtml+xml",
    }
    with httpx.Client(follow_redirects=True, timeout=30.0, headers=headers) as client:
      resp = client.get(url)
    if resp.status_code >= 400:
      raise ValueError(f"Failed to fetch URL (status={resp.status_code})")
    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type:
      if content_type and "html" not in content_type:
        raise ValueError(f"Unsupported content-type: {content_type}")

    raw_text = extract_main_text_from_html(resp.text)
    cleaned = clean_scraped_text(raw_text)
    if len(cleaned) < 200:
      raise ValueError("Page content too short after extraction/cleaning")
  except Exception as e:
    print(f"Primary URL extraction failed, falling back to WebBaseLoader: {e}")
    cleaned = None

  # Fallback: LangChain WebBaseLoader extraction.
  # WebBaseLoader is generic doesn't remove boilerplate html elements doesnt remove scripts or styles. So it is used as a fallback mechanism when the main text falls short of the threshold. 
  if cleaned is None:
    loader = WebBaseLoader(url)
    docs = loader.load()
    merged = "\n\n".join([d.page_content for d in docs if getattr(d, "page_content", "")])
    cleaned = clean_scraped_text(merged)
    if len(cleaned) < 200:
      raise ValueError("Page content too short after fallback extraction/cleaning")

  # Chunk for RAG and persist to python_chat.documents
  splitter = RecursiveCharacterTextSplitter(
    chunk_size=int(chunk_config.get("chunk_size", 800)),
    chunk_overlap=int(chunk_config.get("chunk_overlap", 100)),
  )
  chunks = splitter.split_text(cleaned)
  for i, chunk in enumerate(chunks):
    py_session.add(
      Documents(
        organization_id=str(source.organization_id),
        bot_id=int(source.bot_id),
        source_id=source.id,
        chunk_index=i,
        content=chunk,
      )
    )
  py_session.commit()
  

def _loader_for_path(path: Path):
  ext = path.suffix.lower()
  if ext == ".pdf":
    return PyPDFLoader(str(path))
  if ext == ".csv":
    return CSVLoader(file_path=str(path))
  if ext in (".md", ".txt"):
    # Treat markdown as text for now (keeps deps light).
    return TextLoader(str(path), encoding="utf-8", autodetect_encoding=True)
  raise ValueError(f"Unsupported file type: {ext}. Supported: .csv .md .pdf .txt")


def process_file_training_source(source: TrainingSources, sb_session: Session, py_session: Session,chunk_config: dict) -> None:
  if chunk_config is None:
    chunk_config = {"chunk_size": 800, "chunk_overlap": 100}
  if source.bot_id is None or source.organization_id is None:
    raise ValueError("Training source missing bot_id/organization_id")

  # Find the file record so we know its storage path (and extension).
  file_uuid = uuid.UUID(str(source.source_value))
  file_record = sb_session.scalars(select(Files).where(Files.id == file_uuid)).one_or_none()
  if file_record is None or not file_record.path:
    raise ValueError(f"File record not found for id: {source.source_value}")

  # Get a short-lived signed URL for the private object
  file_signed_url = get_signed_file_url(file_record.bucket or "", file_record.path, expires_in=3600)
  if not file_signed_url:
    raise ValueError(f"Could not create signed URL for file id: {source.source_value}")

  # Download to a temp file, then use LangChain loaders (csv/md/pdf/txt).
  suffix = Path(file_record.path).suffix or ""
  with tempfile.TemporaryDirectory() as tmpdir:
    tmp_path = Path(tmpdir) / f"source{suffix}"
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
      resp = client.get(file_signed_url)
      resp.raise_for_status()
      tmp_path.write_bytes(resp.content)

    loader = _loader_for_path(tmp_path)
    docs = loader.load()
    merged = "\n\n".join([d.page_content for d in docs if getattr(d, "page_content", "")])
    cleaned = clean_scraped_text(merged)
  if len(cleaned) < 50:
    raise ValueError("File content too short after loading/cleaning")

  splitter = RecursiveCharacterTextSplitter(chunk_size=int(chunk_config.get("chunk_size", 800)), chunk_overlap=int(chunk_config.get("chunk_overlap", 100)))
  chunks = splitter.split_text(cleaned)
  for i, chunk in enumerate(chunks):
    py_session.add(
      Documents(
        organization_id=str(source.organization_id),
        bot_id=int(source.bot_id),
        source_id=source.id,
        chunk_index=i,
        content=chunk,
      )
    )
  py_session.commit()
  





def process_training_job(job_id: str, bot_id: int, organization_id: str, source_ids: Sequence[str]) -> None:
    """
    Main worker function that performs the following tasks:
    1. Called by the RQ worker when a training job is enqueued.
    2. Fetches the training job from the database, updates the status to 'processing' and 'started_at' to the current time.
    3. Fetches the training sources from the database, processes each training source, and updates the status to 'completed' or 'failed'.
    4. Updates the training job status to 'completed' or 'failed' and 'completed_at' to the current time.
    5. Rolls back the database sessions if an error occurs.
    6. Closes the database sessions.
    """
    if SessionLocal is None or SupabaseSessionLocal is None:
        print("DB sessions are not configured (missing env vars).")
        return

    py_session = SessionLocal() # connects to the python_chat database
    sb_session = SupabaseSessionLocal() # connects to the supabase database
    job = None
    try:
        job_uuid = uuid.UUID(job_id)
        job = py_session.scalars(select(TrainingJobs).where(TrainingJobs.id == job_uuid)).one()
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        py_session.commit()
        py_session.refresh(job)

        # Fetch the training sources from the database
        source_uuids = [uuid.UUID(sid) for sid in source_ids]
        sources = sb_session.scalars(select(TrainingSources).where(TrainingSources.id.in_(source_uuids))).all()

        for source in sources:
            print(f"Processing training source: {source.id}")
            try:
              if source.type == "url":
                process_url_training_source(source, sb_session, py_session)
              else:
                process_file_training_source(source, sb_session, py_session,chunk_config={"chunk_size": 800, "chunk_overlap": 100})
              source.status = "completed"
              sb_session.commit()
              sb_session.refresh(source)
            except Exception as e:
              print(f"Failed to process training source: {e}")
              source.status = "failed"
              source.error_message = str(e)
              sb_session.commit()
              sb_session.refresh(source)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        py_session.commit()
        py_session.refresh(job)
        print(f"Training job completed: {job_id}")
    except Exception as e:
        print(f"Training job failed: {e}")
        py_session.rollback()
        sb_session.rollback()
        if job is not None:
            try:
                job.status = "failed"
                job.completed_at = datetime.now(timezone.utc)
                py_session.commit()
            except Exception:
                py_session.rollback()
    finally:
        sb_session.close()
        py_session.close()