"""Backward-compatible RQ task exports.

Keep this module path stable because queued RQ jobs serialize callable import
paths. New training implementation belongs in ``app.ingestion``.
"""

from app.config.logging_config import setup_logging
from app.ingestion.cleanup import delete_training_source_job
from app.ingestion.ingestion import (
    _extension_for_loader,
    _loader_for_file,
    process_file_training_source,
    process_url_training_source,
)
from app.ingestion.jobs import process_training_job

setup_logging()

__all__ = [
    "_extension_for_loader",
    "_loader_for_file",
    "delete_training_source_job",
    "process_file_training_source",
    "process_training_job",
    "process_url_training_source",
]
