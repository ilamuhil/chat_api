from __future__ import annotations

import unittest

from app.services.training.ingestion import _extension_for_loader
from app.services.training.jobs import \
    process_training_job as modular_process_job
from app.services.worker_fns import (delete_training_source_job,
                                     process_training_job)


class TrainingWorkerModuleTests(unittest.TestCase):
    def test_legacy_worker_path_reexports_training_task(self) -> None:
        self.assertIs(process_training_job, modular_process_job)

    def test_legacy_worker_path_reexports_cleanup_task(self) -> None:
        self.assertEqual(
            delete_training_source_job.__name__, "delete_training_source_job"
        )

    def test_loader_extension_prefers_filename_then_mime_type(self) -> None:
        self.assertEqual(_extension_for_loader("knowledge.PDF", "text/plain"), ".pdf")
        self.assertEqual(_extension_for_loader(None, "text/markdown; charset=utf-8"), ".md")
        self.assertEqual(_extension_for_loader(None, "application/octet-stream"), "")


if __name__ == "__main__":
    unittest.main()
