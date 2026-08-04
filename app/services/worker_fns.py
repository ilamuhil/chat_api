"""Backward-compatible RQ task exports.

Keep this module path stable because queued RQ jobs serialize callable import
paths. New training implementation belongs in ``app.services.training``.
"""

from app.config.logging_config import setup_logging
from app.services.training.cleanup import delete_training_source_job
from app.services.training.ingestion import (_extension_for_loader,
                                             _loader_for_file,
                                             process_file_training_source,
                                             process_url_training_source)
from app.services.training.jobs import process_training_job

setup_logging()

__all__ = [
    "delete_training_source_job",
    "process_file_training_source",
    "process_training_job",
    "process_url_training_source",
    "_extension_for_loader",
    "_loader_for_file",
]
